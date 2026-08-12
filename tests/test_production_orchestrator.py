from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from tushare_qlib.production_orchestrator import run_production_day
from tushare_qlib.settings import Paths, Settings


def _settings(tmp_path: Path) -> Settings:
    paths = Paths.from_root(tmp_path / "data")
    paths.mkdirs()
    pd.DataFrame(
        {"cal_date": pd.to_datetime(["2026-08-10", "2026-08-11"]), "is_open": [1, 0]}
    ).to_parquet(paths.metadata / "trade_calendar.parquet", index=False)
    return Settings(tmp_path / "pipeline.yaml", {}, paths, None, None, tmp_path / "qlib")


def test_production_orchestrator_dispatches_close_phase(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    calls = []
    monkeypatch.setattr(
        "tushare_qlib.production_orchestrator.run_daily_signal",
        lambda value, **kwargs: calls.append((value, kwargs)) or "close-result",
    )

    result = run_production_day(
        settings, phase="close", business_date="2026-08-10", notify=False, skip_sync=True
    )

    assert result == "close-result"
    assert calls[0][1] == {"as_of": "2026-08-10", "notify": False, "skip_sync": True}


def test_production_orchestrator_rejects_closed_date(tmp_path: Path):
    with pytest.raises(ValueError, match="not an open trading day"):
        run_production_day(
            _settings(tmp_path), phase="pretrade", business_date="2026-08-11", notify=False
        )
