from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from qlib_platform.data.store import sha256_file
from qlib_platform.datasets import qlib_export


def _settings(tmp_path: Path) -> SimpleNamespace:
    root = tmp_path / "root"
    qlib_data = root / "data" / "cn"
    return SimpleNamespace(
        qlib_repo=tmp_path / "qlib-repo",
        qlib_include_fields=["open", "close"],
        qlib_data_uri=qlib_data,
        qlib_versions_root=root / "versions",
        qlib_dataset_ref="current",
        registry_path=root / "registry.sqlite",
        paths=SimpleNamespace(
            root=root,
            staging_full=root / "full",
            staging_update=root / "update",
            staging_repair=root / "repair",
        ),
        data={
            "qlib": {"export": {"backup_keep": 1, "max_workers": 2, "copy_on_write_update": True}},
            "universe": {"instruments": "all"},
        },
    )


def test_read_staging_manifest_verifies_files_and_checksums(tmp_path) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    data = stage / "a.parquet"
    data.write_text("payload", encoding="utf-8")
    manifest = stage / "staging_manifest.json"
    manifest.write_text(json.dumps({"files": {data.name: sha256_file(data)}}), encoding="utf-8")
    assert qlib_export._read_staging_manifest(stage)["files"][data.name] == sha256_file(data)
    manifest.unlink()
    with pytest.raises(FileNotFoundError, match="staging manifest"):
        qlib_export._read_staging_manifest(stage)
    manifest.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        qlib_export._read_staging_manifest(stage)
    manifest.write_text(json.dumps({"files": {}}))
    with pytest.raises(ValueError, match="no files"):
        qlib_export._read_staging_manifest(stage)
    manifest.write_text(json.dumps({"files": {"missing": "x"}}))
    with pytest.raises(FileNotFoundError, match="staging file missing"):
        qlib_export._read_staging_manifest(stage)
    manifest.write_text(json.dumps({"files": {data.name: "bad"}}))
    with pytest.raises(ValueError, match="checksum mismatch"):
        qlib_export._read_staging_manifest(stage)


def test_dump_script_resolution(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(qlib_export, "resolve_qlib_repo", lambda _: None)
    with pytest.raises(RuntimeError, match="QLIB_REPO"):
        qlib_export._dump_script(settings)
    repo = tmp_path / "repo"
    monkeypatch.setattr(qlib_export, "resolve_qlib_repo", lambda _: repo)
    with pytest.raises(FileNotFoundError, match="dump script"):
        qlib_export._dump_script(settings)
    script = repo / "scripts" / "dump_bin.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('ok')", encoding="utf-8")
    assert qlib_export._dump_script(settings) == script


def test_smoke_subprocess_parses_marker_and_errors(monkeypatch, tmp_path) -> None:
    marker = "__TQ_SMOKE_RESULT__="

    def success(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            returncode=0, stdout=f"noise\n{marker}{json.dumps({'calendar_count': 3})}\n", stderr=""
        )

    monkeypatch.setattr(qlib_export.subprocess, "run", success)
    assert qlib_export._smoke_test_dataset_subprocess(tmp_path)["calendar_count"] == 3

    def failure(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(qlib_export.subprocess, "run", failure)
    with pytest.raises(RuntimeError, match="boom"):
        qlib_export._smoke_test_dataset_subprocess(tmp_path)

    def no_marker(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout="nothing", stderr="")

    monkeypatch.setattr(qlib_export.subprocess, "run", no_marker)
    with pytest.raises(RuntimeError, match="no result"):
        qlib_export._smoke_test_dataset_subprocess(tmp_path)

    def bad_result(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout=f"{marker}[]\n", stderr="")

    monkeypatch.setattr(qlib_export.subprocess, "run", bad_result)
    with pytest.raises(RuntimeError, match="must be an object"):
        qlib_export._smoke_test_dataset_subprocess(tmp_path)


def test_portable_dataset_dir_handles_inside_and_external_paths(tmp_path) -> None:
    settings = _settings(tmp_path)
    assert qlib_export._portable_dataset_dir(settings) == "data/cn"
    settings.qlib_data_uri = tmp_path.parent / "external-cn"
    assert qlib_export._portable_dataset_dir(settings) == "external-cn"


def test_replace_directory_atomic_swaps_and_restores_on_failure(tmp_path, monkeypatch) -> None:
    target = tmp_path / "target"
    candidate = tmp_path / "candidate"
    target.mkdir()
    (target / "old").write_text("old")
    candidate.mkdir()
    (candidate / "new").write_text("new")
    backup = qlib_export._replace_directory_atomic(candidate, target)
    assert backup is not None and backup.exists()
    assert (target / "new").is_file()

    target2 = tmp_path / "target2"
    candidate2 = tmp_path / "candidate2"
    target2.mkdir()
    candidate2.mkdir()
    calls = 0
    original = os.replace

    def flaky(source: object, destination: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("cannot publish")
        original(source, destination)

    monkeypatch.setattr(qlib_export.os, "replace", flaky)
    with pytest.raises(OSError, match="cannot publish"):
        qlib_export._replace_directory_atomic(candidate2, target2)
    assert target2.exists()


def test_backup_keep_and_prune(tmp_path) -> None:
    settings = _settings(tmp_path)
    settings.qlib_data_uri.parent.mkdir(parents=True)
    assert qlib_export._backup_keep(settings) == 1
    old = settings.qlib_data_uri.parent / f"{settings.qlib_data_uri.name}.backup.1"
    new = settings.qlib_data_uri.parent / f"{settings.qlib_data_uri.name}.backup.2"
    old.mkdir()
    new.mkdir()
    os.utime(old, (1, 1))
    os.utime(new, (2, 2))
    qlib_export._prune_backups(settings)
    assert new.exists()
    assert not old.exists()
    settings.data["qlib"]["export"]["backup_keep"] = -1
    with pytest.raises(ValueError, match="non-negative"):
        qlib_export._backup_keep(settings)


def test_clone_and_deduplicate_unchanged_dataset(tmp_path) -> None:
    parent_path = tmp_path / "parent"
    candidate = tmp_path / "candidate"
    parent_path.mkdir()
    (parent_path / "same.bin").write_bytes(b"same")
    (parent_path / "different.bin").write_bytes(b"parent")
    parent = SimpleNamespace(data_path=parent_path, reference="version-1")
    qlib_export._clone_base_dataset(parent, candidate)
    assert (candidate / "same.bin").read_bytes() == b"same"
    (candidate / "different.bin").write_bytes(b"changed")
    qlib_export._deduplicate_unchanged(parent, candidate)
    assert (candidate / "same.bin").stat().st_ino == (parent_path / "same.bin").stat().st_ino
    assert (candidate / "different.bin").read_bytes() == b"changed"
    qlib_export._deduplicate_unchanged(SimpleNamespace(reference="legacy", data_path=parent_path), candidate)
    qlib_export._deduplicate_unchanged(None, candidate)


def test_validate_sync_calendar_accepts_ranges_and_rejects_missing_dates(tmp_path) -> None:
    candidate = tmp_path / "candidate"
    calendar = candidate / "calendars" / "day.txt"
    calendar.parent.mkdir(parents=True)
    calendar.write_text("2026-09-01\n2026-09-02\n2026-09-03\n", encoding="utf-8")
    qlib_export._validate_sync_calendar(candidate, None)
    qlib_export._validate_sync_calendar(candidate, {"changed_trade_dates": []})
    qlib_export._validate_sync_calendar(candidate, {"changed_trade_dates": ["20260902", "2026-09-03"]})
    calendar.write_text("2026-09-01\n2026-09-03\n", encoding="utf-8")
    with pytest.raises(ValueError, match="omits changed"):
        qlib_export._validate_sync_calendar(candidate, {"changed_trade_dates": ["20260902"]})
    calendar.unlink()
    with pytest.raises(FileNotFoundError, match="calendar is missing"):
        qlib_export._validate_sync_calendar(candidate, {"changed_trade_dates": ["20260902"]})
    calendar.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="calendar is empty"):
        qlib_export._validate_sync_calendar(candidate, {"changed_trade_dates": ["20260902"]})


def test_dump_update_and_fix_requires_action(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    with pytest.raises(ValueError, match="requires append or repair"):
        qlib_export.dump_update_and_fix(settings, append=False, repair=False)
    base = SimpleNamespace(data_path=tmp_path / "missing", reference="v1")
    monkeypatch.setattr(qlib_export, "resolve_dataset", lambda _: base)
    with pytest.raises(FileNotFoundError, match="base Qlib dataset"):
        qlib_export.dump_update_and_fix(settings, append=True, repair=False)
