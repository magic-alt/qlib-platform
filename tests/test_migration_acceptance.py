from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from qlib_platform.datasets.migration_acceptance import _ohlc_suspension_quality, run_migration_acceptance
from qlib_platform.settings import Settings


def _settings(tmp_path: Path) -> Settings:
    config = tmp_path / "pipeline.yaml"
    config.write_text(
        "\n".join(
            [
                "mode: standalone",
                f"project_root: {tmp_path / 'data'}",
                "data_source: {kind: auto}",
                "qlib:",
                "  dataset_name: test_cn",
                "  dataset_ref: research-current",
            ]
        ),
        encoding="utf-8",
    )
    return Settings.load(config)


def _provider(root: Path) -> Path:
    (root / "calendars").mkdir(parents=True)
    (root / "instruments").mkdir()
    (root / "features" / "sh600000").mkdir(parents=True)
    (root / "calendars" / "day.txt").write_text("2026-08-24\n", encoding="utf-8")
    (root / "instruments" / "all.txt").write_text("SH600000\t2026-08-24\t2026-08-24\n", encoding="utf-8")
    (root / "features" / "sh600000" / "close.day.bin").write_bytes(b"close")
    return root


def test_qlib_migration_acceptance_is_isolated_and_records_evidence(tmp_path: Path):
    settings = _settings(tmp_path)
    source = _provider(tmp_path / "legacy")
    original = {
        path.relative_to(source).as_posix(): path.read_bytes() for path in source.rglob("*") if path.is_file()
    }
    target = tmp_path / "acceptance"

    evidence_path = run_migration_acceptance(
        settings,
        source_kind="qlib",
        source_root=source,
        acceptance_root=target,
    )

    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["dataReleaseId"].startswith("ds_")
    assert len(evidence["dataReleaseId"]) == 67
    assert evidence["dataReleaseProfile"] == "ashare_qlib_import_v1"
    assert evidence["releaseVerification"]["mode"] == "deep"
    assert evidence["datasetVerification"]["mode"] == "deep"
    assert evidence["cas"]["uniqueObjectCount"] == 3
    assert evidence["cas"]["hardlinkRatio"] == 1.0
    assert evidence["datasetVerification"]["verifiedViaCasCount"] == 3
    assert evidence["ohlcSuspensionQuality"]["status"] == "NOT_APPLICABLE"
    assert evidence["downstream"]["researchAudit"] == "PENDING"
    assert original == {
        path.relative_to(source).as_posix(): path.read_bytes() for path in source.rglob("*") if path.is_file()
    }


def test_migration_acceptance_rejects_overlap_and_nonempty_target(tmp_path: Path):
    settings = _settings(tmp_path)
    source = _provider(tmp_path / "legacy")

    with pytest.raises(ValueError, match="must not overlap"):
        run_migration_acceptance(
            settings,
            source_kind="qlib",
            source_root=source,
            acceptance_root=source / "acceptance",
        )

    target = tmp_path / "occupied"
    target.mkdir()
    (target / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="absent or empty"):
        run_migration_acceptance(
            settings,
            source_kind="qlib",
            source_root=source,
            acceptance_root=target,
        )


def test_ohlc_suspension_quality_separates_explained_and_unexplained(tmp_path: Path):
    staging = tmp_path / "staging.parquet"
    status = tmp_path / "status.parquet"
    pd.DataFrame(
        {
            "date": ["2026-08-24", "2026-08-24", "2026-08-24"],
            "symbol": ["SH600000", "SZ000001", "SH600001"],
            "open": [None, None, 10.0],
            "close": [None, None, 10.1],
            "paused": [1.0, 0.0, 0.0],
        }
    ).to_parquet(staging, index=False)
    pd.DataFrame({"trade_date": ["20260824"], "ts_code": ["600000.SH"], "suspend_type": ["S"]}).to_parquet(
        status, index=False
    )
    release = SimpleNamespace(files=lambda role: [staging] if role == "qlib_staging" else [status])

    result = _ohlc_suspension_quality(release)

    assert result == {
        "missingOpenOrClose": 2,
        "explainedByPaused": 1,
        "confirmedByTradeStatus": 1,
        "unexplainedMissingOhlc": 1,
        "passed": False,
    }


def test_research_migration_rejects_dirty_lineage_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    settings = _settings(tmp_path)
    source = tmp_path / "legacy-data"
    source.mkdir()
    target = tmp_path / "acceptance"
    monkeypatch.setattr(
        "qlib_platform.datasets.migration_acceptance.git_revision",
        lambda _path: {"commit": "a" * 40, "dirty": True},
    )

    with pytest.raises(ValueError, match="requires clean"):
        run_migration_acceptance(
            settings,
            source_kind="research",
            source_root=source,
            acceptance_root=target,
            start="2026-08-01",
            end="2026-08-24",
        )

    assert not target.exists()
