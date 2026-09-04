from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from qlib_platform.bootstrap import bootstrap
from qlib_platform.datasets.data_source_resolver import ReleaseSelectionRequired, SourceResolution
from qlib_platform.datasets.dataset_registry import DatasetRegistry
from qlib_platform.settings import Paths, Settings


def _settings(tmp_path: Path, *, token: str | None = None) -> Settings:
    paths = Paths.from_root(tmp_path / "data")
    return Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={
            "mode": "standalone",
            "start_date": "20260101",
            "end_date": "20260824",
            "data_source": {"kind": "auto"},
            "release_store": {"active_keep": 1},
            "qlib": {},
        },
        paths=paths,
        tushare_token=token,
        qlib_repo=None,
        qlib_data_uri=paths.root / "qlib" / "current",
    )


def test_auto_bootstrap_returns_release_selection_guidance_for_explicit_guard(
    tmp_path: Path, monkeypatch
) -> None:
    def ambiguous_release(_settings: Settings):
        raise ReleaseSelectionRequired(
            "RELEASE_SELECTION_REQUIRED: multiple DataReleases exist without an active alias"
        )

    monkeypatch.setattr("qlib_platform.bootstrap.resolve_source", ambiguous_release)

    result = bootstrap(_settings(tmp_path), source="auto")

    assert result["status"] == "RELEASE_SELECTION_REQUIRED"
    assert result["recommendedCommand"] == "tq release list"
    assert result["selectionCommand"] == (
        "tq release promote <DATA_RELEASE_ID> --alias research-release-current"
    )
    assert result["retryCommand"] == "tq-research prepare --source auto"


def test_tushare_bootstrap_uses_configured_window(tmp_path: Path, monkeypatch):
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        "qlib_platform.bootstrap._run_cli",
        lambda _settings, *arguments: calls.append(tuple(arguments)),
    )
    monkeypatch.setattr(
        "qlib_platform.bootstrap.resolve_source",
        lambda _settings: SourceResolution("READY", "data_release", "ds_ready"),
    )
    monkeypatch.setattr("qlib_platform.bootstrap._archive_standalone_history", lambda *_args: 0)

    result = bootstrap(_settings(tmp_path, token="dummy_test_token"), source="tushare")

    assert result["status"] == "READY"
    assert result["source"] == "tushare"
    assert ("backfill", "--start", "20260101", "--end", "20260824") in calls


def test_tushare_bootstrap_builds_all_required_release_inputs(tmp_path: Path, monkeypatch):
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        "qlib_platform.bootstrap._run_cli",
        lambda _settings, *arguments: calls.append(tuple(arguments)),
    )
    monkeypatch.setattr(
        "qlib_platform.bootstrap.resolve_source",
        lambda _settings: SourceResolution("READY", "data_release", "ds_ready"),
    )
    monkeypatch.setattr("qlib_platform.bootstrap._archive_standalone_history", lambda *_args: 0)

    result = bootstrap(
        _settings(tmp_path, token="dummy_test_token"),
        source="tushare",
        start="20260101",
        end="20260824",
    )

    assert result["status"] == "READY"
    assert ("sync-dividends", "--bootstrap") in calls
    assert ("sync-industry", "--end", "20260824") in calls
    assert calls[-1] == ("dataset-build", "--start", "20260101", "--end", "20260824")


def test_auto_bootstrap_materializes_selected_release_when_no_dataset_exists(
    tmp_path: Path, monkeypatch
) -> None:
    release_id = "ds_" + "a" * 64
    calls: list[str] = []
    monkeypatch.setattr(
        "qlib_platform.bootstrap.resolve_source",
        lambda _settings: SourceResolution(
            "MATERIALIZE_REQUIRED",
            "data_release",
            release_id,
            action="dataset-materialize",
            profile="ashare_qlib_research_v2",
        ),
    )
    monkeypatch.setattr(
        "qlib_platform.bootstrap._recover_selected_dataset_alias",
        lambda _settings, _release_id: None,
    )

    def materialize(_settings: Settings, selected: str):
        calls.append(selected)
        return {
            "status": "READY",
            "source": "data_release",
            "reference": "standalone-current",
            "dataReleaseId": selected,
            "datasetVersionId": "dv_materialized",
            "materialized": True,
        }

    monkeypatch.setattr("qlib_platform.bootstrap._materialize_selected_release", materialize)

    result = bootstrap(_settings(tmp_path), source="auto")

    assert result["status"] == "READY"
    assert result["materialized"] is True
    assert result["datasetVersionId"] == "dv_materialized"
    assert calls == [release_id]


def test_auto_bootstrap_recovers_dataset_alias_after_explicit_release_selection(
    tmp_path: Path, monkeypatch
) -> None:
    settings = _settings(tmp_path)
    registry = DatasetRegistry(settings.registry_path)
    release_id = "ds_selected"
    release_manifest = tmp_path / "release.json"
    release_manifest.write_text("{}", encoding="utf-8")
    registry.register_release(
        SimpleNamespace(
            data_release_id=release_id,
            profile="ashare_qlib_import_v1",
            manifest_path=release_manifest,
            manifest_sha256="0" * 64,
            manifest={},
            coverage={},
        ),
        governance_level="exploratory",
    )
    data_path = tmp_path / "dataset"
    data_path.mkdir()
    manifest_path = data_path / "dataset_manifest.json"
    manifest = {
        "schema_version": "3.0",
        "version_id": "dv_selected",
        "dataset_name": settings.qlib_dataset_name,
        "layer": "qlib",
        "status": "VALIDATED",
        "data_path": str(data_path),
        "data_release_id": release_id,
        "coverage": {},
        "partitions": [],
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    registry.register_dataset(manifest, manifest_path)
    registry.promote_release("research-release-current", release_id)
    monkeypatch.setattr(
        "qlib_platform.bootstrap.resolve_source",
        lambda _settings: SourceResolution(
            "MATERIALIZE_REQUIRED",
            "data_release",
            release_id,
            action="dataset-materialize",
        ),
    )
    monkeypatch.setattr("qlib_platform.bootstrap._archive_standalone_history", lambda *_args: 0)

    result = bootstrap(settings, source="auto")

    assert result["status"] == "READY"
    assert result["aliasRecovered"] is True
    assert result["dataReleaseId"] == release_id
    assert result["datasetVersionId"] == "dv_selected"
    assert registry.resolve(settings.qlib_dataset_ref).version_id == "dv_selected"
