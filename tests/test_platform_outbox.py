from __future__ import annotations

from pathlib import Path

from qlib_platform.platform_adapter import ArtifactOutbox, PlatformClient


def test_outbox_survives_platform_failure_and_acks_after_recovery(tmp_path: Path):
    artifact = tmp_path / "artifact.json"
    artifact.write_text("{}", encoding="utf-8")
    release_id = "ds_" + "a" * 64
    outbox = ArtifactOutbox(tmp_path / "state" / "outbox.sqlite")
    item = outbox.enqueue(artifact, release_id)
    artifact.unlink()

    assert item.artifact_path.is_file()
    assert "spool" in item.artifact_path.parts

    def unavailable(_item):
        raise ConnectionError("platform unavailable")

    assert outbox.drain(unavailable) == 0
    assert outbox.pending()[0].attempts == 1
    sent: list[str] = []
    assert outbox.drain(lambda value: sent.append(value.item_id)) == 1
    assert sent == [item.item_id]
    assert outbox.pending() == []


def test_outbox_is_idempotent_for_same_artifact_and_release(tmp_path: Path):
    artifact = tmp_path / "artifact.json"
    artifact.write_text("{}", encoding="utf-8")
    outbox = ArtifactOutbox(tmp_path / "outbox.sqlite")
    release_id = "ds_" + "b" * 64

    assert outbox.enqueue(artifact, release_id).item_id == outbox.enqueue(artifact, release_id).item_id


def test_platform_client_sends_contract_identity_headers(tmp_path: Path, monkeypatch):
    artifact = tmp_path / "artifact.json"
    artifact.write_text('{"schemaVersion":"2.0"}', encoding="utf-8")
    item = ArtifactOutbox(tmp_path / "outbox.sqlite").enqueue(artifact, "ds_" + "c" * 64)
    captured = {}

    class Response:
        status = 202

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    def fake_open(request, *, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("qlib_platform.platform_adapter.client.urlopen", fake_open)
    PlatformClient("https://platform.example/artifacts", timeout_seconds=4).send(item)

    request = captured["request"]
    assert captured["timeout"] == 4
    assert request.get_header("Idempotency-key") == item.item_id
    assert request.get_header("X-artifact-sha256") == item.artifact_sha256
    assert request.get_header("X-data-release-id") == item.data_release_id
