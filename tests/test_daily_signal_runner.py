from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tushare_qlib.daily_signal_runner import run_daily_signal
from tushare_qlib.settings import Paths, Settings


def test_missing_notifier_config_records_failed_run(tmp_path: Path, monkeypatch):
    settings = Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={},
        paths=Paths.from_root(tmp_path / "data"),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib",
    )
    settings.paths.mkdirs()
    monkeypatch.setattr(
        "tushare_qlib.daily_signal_runner.feishu_notifier_from_environment",
        lambda: (_ for _ in ()).throw(ValueError("FEISHU_WEBHOOK_URL is required")),
    )

    with pytest.raises(ValueError, match="FEISHU_WEBHOOK_URL"):
        run_daily_signal(settings, as_of="2026-08-10", skip_sync=True)

    with sqlite3.connect(settings.paths.state / "ops.sqlite3") as connection:
        status, details = connection.execute(
            "SELECT status, details_json FROM pipeline_runs"
        ).fetchone()
    assert status == "FAILED"
    assert "ValueError" in details
