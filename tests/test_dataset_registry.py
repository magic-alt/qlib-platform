from __future__ import annotations

import json
from pathlib import Path

import pytest

from qlib_platform.dataset_manifest import verify_dataset_manifest, write_dataset_manifest
from qlib_platform.dataset_registry import DatasetRegistry
from qlib_platform.dataset_resolver import resolve_dataset
from qlib_platform.dataset_resolver import pin_dataset
from qlib_platform.settings import Paths, Settings


def _settings(tmp_path: Path) -> Settings:
    paths = Paths.from_root(tmp_path / "data")
    paths.mkdirs()
    return Settings(
        tmp_path / "pipeline.yaml",
        {"qlib": {"dataset_name": "test", "dataset_ref": "research-current"}},
        paths,
        None,
        None,
        tmp_path / "legacy",
    )


def _dataset(settings: Settings, value: str = "one") -> tuple[Path, dict[str, object]]:
    candidate = settings.qlib_versions_root / f"candidate-{value}"
    candidate.mkdir(parents=True)
    (candidate / "value.bin").write_text(value, encoding="utf-8")
    path, payload = write_dataset_manifest(
        candidate,
        dataset_name="test",
        layer="qlib",
        semantic_contract={"fields": ["close"], "pit": "next_trading_day"},
        coverage={"start": "2026-01-01", "end": "2026-01-02"},
    )
    payload["status"] = "VALIDATED"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path, payload


def test_registry_promotes_and_resolves_only_published_version(tmp_path: Path):
    settings = _settings(tmp_path)
    manifest, payload = _dataset(settings)
    registry = DatasetRegistry(settings.registry_path)
    registry.initialize()
    registry.register_dataset(payload, manifest)

    with pytest.raises(ValueError, match="not published"):
        registry.resolve(str(payload["version_id"]))

    registry.promote("research-current", str(payload["version_id"]))
    resolved = resolve_dataset(settings, allow_legacy=False)

    assert resolved.version_id == payload["version_id"]
    assert resolved.data_path == manifest.parent.resolve()


def test_manifest_version_is_timestamp_and_row_order_independent(tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "a.bin").write_bytes(b"a")
    (second / "a.bin").write_bytes(b"a")
    _, one = write_dataset_manifest(
        first, dataset_name="test", layer="qlib", semantic_contract={"b": 2, "a": 1}
    )
    _, two = write_dataset_manifest(
        second, dataset_name="test", layer="qlib", semantic_contract={"a": 1, "b": 2}
    )

    assert one["version_id"] == two["version_id"]
    assert one["build_id"] != two["build_id"]


def test_manifest_verification_detects_partition_tampering(tmp_path: Path):
    settings = _settings(tmp_path)
    manifest, _ = _dataset(settings)
    verify_dataset_manifest(manifest)
    (manifest.parent / "value.bin").write_text("tampered", encoding="utf-8")

    with pytest.raises(ValueError, match="checksum mismatch"):
        verify_dataset_manifest(manifest)


def test_alias_is_unchanged_when_new_version_is_not_promoted(tmp_path: Path):
    settings = _settings(tmp_path)
    first_manifest, first = _dataset(settings, "first")
    second_manifest, second = _dataset(settings, "second")
    registry = DatasetRegistry(settings.registry_path)
    registry.initialize()
    registry.register_dataset(first, first_manifest)
    registry.promote("research-current", str(first["version_id"]))
    registry.register_dataset(second, second_manifest)

    assert registry.resolve("research-current").version_id == first["version_id"]


def test_promoting_new_alias_does_not_mutate_immutable_dataset_manifest(tmp_path: Path):
    settings = _settings(tmp_path)
    manifest, payload = _dataset(settings)
    registry = DatasetRegistry(settings.registry_path)
    registry.initialize()
    registry.register_dataset(payload, manifest)
    original = manifest.read_bytes()

    registry.promote("research-current", str(payload["version_id"]))

    assert manifest.read_bytes() == original
    pointer = settings.registry_path.parent / "aliases" / "research-current.json"
    assert json.loads(pointer.read_text(encoding="utf-8"))["version_id"] == payload["version_id"]


def test_pipeline_run_records_success_and_failure(tmp_path: Path):
    settings = _settings(tmp_path)
    registry = DatasetRegistry(settings.registry_path)
    registry.start_pipeline_run("success", "dataset_build")
    registry.finish_pipeline_run("success", status="SUCCEEDED")
    registry.start_pipeline_run("failure", "daily_sync")
    registry.finish_pipeline_run("failure", status="FAILED", error_code="InjectedError")

    with registry.connect() as connection:
        rows = connection.execute(
            "SELECT run_id,status,error_code FROM pipeline_runs ORDER BY run_id"
        ).fetchall()

    assert [(row["run_id"], row["status"], row["error_code"]) for row in rows] == [
        ("failure", "FAILED", "InjectedError"),
        ("success", "SUCCEEDED", None),
    ]


def test_explicitly_pinned_path_is_not_replaced_by_current_alias(tmp_path: Path):
    from dataclasses import replace

    settings = _settings(tmp_path)
    current_manifest, current = _dataset(settings, "current")
    pinned_manifest, pinned = _dataset(settings, "pinned")
    registry = DatasetRegistry(settings.registry_path)
    registry.initialize()
    for manifest, payload in ((current_manifest, current), (pinned_manifest, pinned)):
        payload["status"] = "PUBLISHED"
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        registry.register_dataset(payload, manifest)
    registry.promote("research-current", str(current["version_id"]))

    pinned_settings = replace(settings, qlib_data_uri=pinned_manifest.parent.resolve())
    resolved_settings, resolved = pin_dataset(pinned_settings)

    assert resolved.version_id == pinned["version_id"]
    assert resolved_settings.qlib_data_uri == pinned_manifest.parent.resolve()


def test_explicit_legacy_dataset_uri_remains_supported(tmp_path: Path):
    from dataclasses import replace

    settings = _settings(tmp_path)
    configured = tmp_path / "configured"
    settings.data["qlib"]["dataset_dir"] = str(configured)
    explicit = tmp_path / "frozen-v2"
    explicit.mkdir()
    (explicit / "dataset_manifest.json").write_text(
        json.dumps({"schema_version": "2.0", "dataset_id": "legacy", "sha256": "legacy-hash"}),
        encoding="utf-8",
    )

    _, resolved = pin_dataset(replace(settings, qlib_data_uri=explicit.resolve()))

    assert resolved.reference == "explicit-path"
    assert resolved.version_id == "legacy-hash"
    assert resolved.data_path == explicit.resolve()
