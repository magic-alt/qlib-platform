from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from tushare_qlib.settings import Paths, Settings
from tushare_qlib.signal_health import evaluate_signal_health


def _settings(tmp_path: Path) -> Settings:
    paths = Paths.from_root(tmp_path / "data")
    paths.mkdirs()
    qlib = tmp_path / "qlib"
    qlib.mkdir()
    (qlib / "dataset_manifest.json").write_text(
        json.dumps({"smoke_test": {"last_date": "2026-08-10"}}), encoding="utf-8"
    )
    sync = paths.state / "daily_sync"
    sync.mkdir(parents=True)
    (sync / "latest.json").write_text(
        json.dumps({"status": "published", "eligible_date": "2026-08-10"}), encoding="utf-8"
    )
    (sync / "pending_publish.json").write_text(json.dumps({"status": "clear"}), encoding="utf-8")
    return Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={
            "production": {"max_model_age_days": 45, "min_cross_section_coverage": 0.8},
            "strategy": {"topk_dropout": {"topk": 2, "n_drop": 1}},
        },
        paths=paths,
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=qlib,
    )


def test_signal_health_passes_fresh_complete_cross_section(tmp_path: Path):
    settings = _settings(tmp_path)
    report = evaluate_signal_health(
        settings,
        pd.Series([0.3, 0.2, 0.1], index=["A", "B", "C"]),
        signal_date="2026-08-10",
        trade_date="2026-08-11",
        deployment={"status": "DEPLOYED"},
        bundle_manifest={
            "createdAtUtc": "2026-08-10T10:00:00Z",
            "referenceCrossSectionCount": 3,
        },
        now_utc=datetime(2026, 8, 10, 12, tzinfo=timezone.utc),
    )
    assert report.passed
    assert report.reasons == []


def test_signal_health_rejects_stale_and_degenerate_signal(tmp_path: Path):
    settings = _settings(tmp_path)
    report = evaluate_signal_health(
        settings,
        pd.Series([0.1, 0.1], index=["A", "B"]),
        signal_date="2026-08-09",
        trade_date="2026-08-08",
        deployment={"status": "RETIRED"},
        bundle_manifest={
            "createdAtUtc": "2026-01-01T00:00:00Z",
            "referenceCrossSectionCount": 10,
        },
        now_utc=datetime(2026, 8, 10, 12, tzinfo=timezone.utc),
    )
    assert not report.passed
    assert {
        "DATASET_LAST_DATE_MISMATCH",
        "MODEL_NOT_DEPLOYED",
        "MODEL_STALE",
        "DEGENERATE_SCORE",
        "CROSS_SECTION_TOO_SMALL",
        "INVALID_TRADE_DATE",
        "DAILY_SYNC_DATE_MISMATCH",
    }.issubset(report.reasons)
