from __future__ import annotations

from pathlib import Path

import pytest

from qlib_platform.bootstrap import bootstrap
from qlib_platform.datasets.data_source_resolver import SourceResolution
from qlib_platform.datasets.qlib_staging_contract import QlibStagingContractError
from qlib_platform.settings import Paths, Settings


def _settings(tmp_path: Path, *, mode: str = "standalone") -> Settings:
    paths = Paths.from_root(tmp_path / "data")
    return Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={
            "mode": mode,
            "start_date": "20260101",
            "end_date": "20260824",
            "data_source": {"kind": "auto"},
            "release_store": {"active_keep": 1},
            "qlib": {
                "dataset_name": "cn_standalone",
                "dataset_ref": "standalone-current",
            },
        },
        paths=paths,
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=paths.root / "qlib" / "current",
    )


def test_auto_bootstrap_rebuilds_bad_staging_from_certified_raw(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    release_id = "ds_" + "a" * 64
    resolutions = iter(
        [
            SourceResolution(
                "MATERIALIZE_REQUIRED",
                "data_release",
                release_id,
                action="dataset-materialize",
                profile="ashare_qlib_research_v2",
            ),
            SourceResolution(
                "READY",
                "dataset_version",
                settings.qlib_dataset_ref,
                path=settings.qlib_data_uri,
            ),
        ]
    )
    calls: list[tuple[str, ...]] = []

    monkeypatch.setattr("qlib_platform.bootstrap.resolve_source", lambda _settings: next(resolutions))
    monkeypatch.setattr(
        "qlib_platform.bootstrap._recover_selected_dataset_alias",
        lambda _settings, _release_id: None,
    )
    monkeypatch.setattr(
        "qlib_platform.bootstrap._materialize_selected_release",
        lambda *_args: (_ for _ in ()).throw(
            QlibStagingContractError("qlib_staging file must contain date and symbol columns: 00000.parquet")
        ),
    )
    monkeypatch.setattr(
        "qlib_platform.bootstrap.resolve_local_raw_source",
        lambda _settings: SourceResolution(
            "BUILD_REQUIRED",
            "local_raw",
            path=settings.paths.raw,
            action="release build-local",
            profile="ashare_qlib_research_v2",
        ),
    )
    monkeypatch.setattr(
        "qlib_platform.bootstrap._run_cli",
        lambda _settings, *arguments: calls.append(tuple(arguments)),
    )

    result = bootstrap(settings, source="auto")

    assert result["status"] == "READY"
    assert result["reference"] == "standalone-current"
    assert result["recoveredFromIncompatibleRelease"] is True
    assert result["recoveryReason"] == "qlib_staging_contract"
    assert calls == [("dataset-build", "--start", "20260101", "--end", "20260824")]


def test_auto_bootstrap_fails_closed_when_certified_raw_is_incomplete(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    release_id = "ds_" + "b" * 64
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
    monkeypatch.setattr(
        "qlib_platform.bootstrap._materialize_selected_release",
        lambda *_args: (_ for _ in ()).throw(
            QlibStagingContractError("qlib_staging file must contain date and symbol columns: 00000.parquet")
        ),
    )
    monkeypatch.setattr(
        "qlib_platform.bootstrap.resolve_local_raw_source",
        lambda _settings: SourceResolution(
            "DATA_INCOMPLETE",
            "local_raw",
            path=settings.paths.raw,
            action="add required local market components",
            profile="ashare_market_import_v1",
            missing_components=("daily_basic",),
        ),
    )
    monkeypatch.setattr(
        "qlib_platform.bootstrap._run_cli",
        lambda *_args: pytest.fail("dataset-build must not run with incomplete certified raw data"),
    )

    result = bootstrap(settings, source="auto")

    assert result["status"] == "DATA_INCOMPATIBLE"
    assert result["reference"] == "standalone-current"
    assert result["missingComponents"] == ["daily_basic"]
    assert "00000.parquet" in result["error"]


def test_integrated_bootstrap_does_not_auto_rebuild_bad_immutable_release(
    tmp_path: Path, monkeypatch
) -> None:
    settings = _settings(tmp_path, mode="integrated")
    release_id = "ds_" + "c" * 64
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
    monkeypatch.setattr(
        "qlib_platform.bootstrap._materialize_selected_release",
        lambda *_args: (_ for _ in ()).throw(
            QlibStagingContractError("qlib_staging file must contain date and symbol columns: 00000.parquet")
        ),
    )

    with pytest.raises(QlibStagingContractError, match="00000.parquet"):
        bootstrap(settings, source="auto")
