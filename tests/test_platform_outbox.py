from __future__ import annotations

from pathlib import Path

from tushare_qlib.platform_adapter import ArtifactOutbox


def test_outbox_survives_platform_failure_and_acks_after_recovery(tmp_path: Path):
    artifact = tmp_path / "artifact.json"
    artifact.write_text("{}", encoding="utf-8")
    release_id = "ds_" + "a" * 64
    outbox = ArtifactOutbox(tmp_path / "state" / "outbox.sqlite")
    item = outbox.enqueue(artifact, release_id)

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
