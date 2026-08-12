from __future__ import annotations

from pathlib import Path

import pandas as pd

from tushare_qlib.production_refit import production_refit_windows
from tushare_qlib.settings import Paths, Settings


def test_refit_windows_end_at_latest_fully_labelled_date(tmp_path: Path, monkeypatch):
    dates = pd.bdate_range("2020-01-01", periods=240)
    settings = Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={
            "research": {
                "label_horizon_days": 5,
                "signal_lag_days": 1,
                "walk_forward": {
                    "train_days": 100,
                    "valid_days": 20,
                    "purge_days": 6,
                    "embargo_days": 6,
                },
            }
        },
        paths=Paths.from_root(tmp_path / "data"),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib",
    )
    monkeypatch.setattr("tushare_qlib.production_refit.shared_research_calendar", lambda value: dates)

    train, valid = production_refit_windows(settings, dates[-1].strftime("%Y-%m-%d"))

    assert valid[1] == dates[-7].strftime("%Y-%m-%d")
    assert len(dates[(dates >= valid[0]) & (dates <= valid[1])]) == 20
    assert len(dates[(dates >= train[0]) & (dates <= train[1])]) == 100
    assert dates.get_loc(valid[0]) - dates.get_loc(train[1]) - 1 == 6
