from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from qlib_platform.daily_signal_runner import run_daily_signal
from qlib_platform.settings import Paths, Settings


class _Notifier:
    channel = "feishu"

    def __init__(self) -> None:
        self.messages = []

    def send(self, envelope) -> None:
        self.messages.append(envelope)


def _settings(tmp_path: Path) -> Settings:
    paths = Paths.from_root(tmp_path / "data")
    paths.mkdirs()
    return Settings(
        tmp_path / "pipeline.yaml",
        {"production": {"notification": {"retry_delays_seconds": [0]}}},
        paths,
        None,
        None,
        tmp_path / "qlib",
    )


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (FileNotFoundError("dataset manifest missing"), "DATA_NOT_READY"),
        (RuntimeError("no DEPLOYED model is registered"), "MODEL_NOT_DEPLOYED"),
        (ValueError("model bundle checksum mismatch"), "MODEL_LOAD_FAILED"),
    ],
)
def test_close_failures_do_not_release_signal_and_emit_domain_alert(
    tmp_path: Path, monkeypatch, error: Exception, code: str
):
    settings = _settings(tmp_path)
    notifier = _Notifier()
    monkeypatch.setattr("qlib_platform.daily_signal_runner.feishu_notifier_from_environment", lambda: notifier)
    monkeypatch.setattr(
        "qlib_platform.daily_signal_runner.run_live_inference",
        lambda *args, **kwargs: (_ for _ in ()).throw(error),
    )

    with pytest.raises(type(error)):
        run_daily_signal(settings, as_of="2026-08-11", skip_sync=True)

    with sqlite3.connect(settings.paths.state / "ops.sqlite3") as connection:
        run = connection.execute("SELECT status, details_json FROM pipeline_runs").fetchone()
        signal_count = connection.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    assert run[0] == "FAILED"
    assert code in run[1]
    assert signal_count == 0
    assert [item.message_kind for item in notifier.messages] == [f"FAILURE_{code}"]
