from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from qlib_platform.daily_signal_runner import run_daily_signal
from qlib_platform.live_inference import LiveInferenceResult
from qlib_platform.settings import Paths, Settings
from qlib_platform.signal_health import SignalHealthReport


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
        "qlib_platform.daily_signal_runner.feishu_notifier_from_environment",
        lambda: (_ for _ in ()).throw(ValueError("FEISHU_WEBHOOK_URL is required")),
    )

    with pytest.raises(ValueError, match="FEISHU_WEBHOOK_URL"):
        run_daily_signal(settings, as_of="2026-08-10", skip_sync=True)

    with sqlite3.connect(settings.paths.state / "ops.sqlite3") as connection:
        status, details = connection.execute("SELECT status, details_json FROM pipeline_runs").fetchone()
    assert status == "FAILED"
    assert "PIPELINE_FAILED" in details


def _live_result(tmp_path: Path) -> LiveInferenceResult:
    score = tmp_path / "model_score.parquet"
    topk = tmp_path / "model_topk.csv"
    manifest = tmp_path / "manifest.json"
    pd.DataFrame({"payload_sha256": ["score-sha"]}).to_parquet(score, index=False)
    pd.DataFrame({"instrument": ["SH600000"], "score_rank": [1], "score": [0.3]}).to_csv(topk, index=False)
    manifest.write_text("{}", encoding="utf-8")
    return LiveInferenceResult(
        signal_id="signal-1",
        signal_date="2026-08-10",
        trade_date="2026-08-11",
        deployment_id="model-1",
        score_path=score,
        topk_path=topk,
        manifest_path=manifest,
        health=SignalHealthReport(True, "PASS", [], {}),
        created=True,
    )


def test_daily_signal_full_call_path_is_idempotent(tmp_path: Path, monkeypatch):
    settings = Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={"production": {"notification": {"retry_delays_seconds": [0]}}},
        paths=Paths.from_root(tmp_path / "data"),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib",
    )
    settings.paths.mkdirs()
    calls = []
    sent = []

    class Notifier:
        channel = "feishu"

        def send(self, envelope):
            sent.append(envelope)

    monkeypatch.setattr(
        "qlib_platform.daily_signal_runner.run_daily_sync",
        lambda value, as_of: calls.append(("sync", as_of)),
    )
    monkeypatch.setattr(
        "qlib_platform.daily_signal_runner.run_live_inference",
        lambda value, **kwargs: calls.append(("inference", kwargs["as_of"])) or _live_result(tmp_path),
    )
    monkeypatch.setattr(
        "qlib_platform.daily_signal_runner.feishu_notifier_from_environment", lambda: Notifier()
    )

    run_daily_signal(settings, as_of="2026-08-10")
    run_daily_signal(settings, as_of="2026-08-10")

    assert calls == [
        ("sync", "2026-08-10"),
        ("inference", "2026-08-10"),
        ("sync", "2026-08-10"),
        ("inference", "2026-08-10"),
    ]
    assert len(sent) == 1
    assert sent[0].message_kind == "SIGNAL_PREVIEW"
    with sqlite3.connect(settings.paths.state / "ops.sqlite3") as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM pipeline_runs WHERE status = 'PASS'").fetchone()[0] == 2
        )
        assert connection.execute("SELECT COUNT(*) FROM deliveries WHERE status = 'SENT'").fetchone()[0] == 1


def test_daily_sync_failure_produces_domain_alert(tmp_path: Path, monkeypatch):
    settings = Settings(
        tmp_path / "pipeline.yaml",
        {"production": {"notification": {"retry_delays_seconds": [0]}}},
        Paths.from_root(tmp_path / "data"),
        None,
        None,
        tmp_path / "qlib",
    )
    settings.paths.mkdirs()
    sent = []

    class Notifier:
        channel = "feishu"

        def send(self, envelope):
            sent.append(envelope)

    monkeypatch.setattr(
        "qlib_platform.daily_signal_runner.feishu_notifier_from_environment", lambda: Notifier()
    )
    monkeypatch.setattr(
        "qlib_platform.daily_signal_runner.run_daily_sync",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("upstream timeout")),
    )

    with pytest.raises(TimeoutError):
        run_daily_signal(settings, as_of="2026-08-10")

    assert [envelope.message_kind for envelope in sent] == ["FAILURE_DAILY_SYNC_FAILED"]
