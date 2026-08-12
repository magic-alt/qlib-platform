from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from tushare_qlib.production_refit import (
    _fit_final_model,
    _selected_training_steps,
    production_refit_plan,
    production_refit_windows,
)
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

    assert train[1] == dates[-7].strftime("%Y-%m-%d")
    assert valid[1] == train[1]
    assert len(dates[(dates >= valid[0]) & (dates <= valid[1])]) == 20
    assert dates.get_loc(train[1]) > dates.get_loc(valid[0])

    plan = production_refit_plan(settings, dates[-1].strftime("%Y-%m-%d"))
    assert len(dates[(dates >= plan.selection_train[0]) & (dates <= plan.selection_train[1])]) == 100
    assert dates.get_loc(plan.selection_valid[0]) - dates.get_loc(plan.selection_train[1]) - 1 == 6
    assert plan.final_train == train
    assert plan.label_safe_end == dates[-7].strftime("%Y-%m-%d")


def test_final_refit_uses_selected_steps_without_validation_segment():
    calls = []

    class Model:
        model = SimpleNamespace(best_iteration=37)
        num_boost_round = 2000

        def fit(self, dataset, **kwargs):
            calls.append((dict(dataset.segments), kwargs))

    dataset = SimpleNamespace(segments={"train": ("2020", "2026"), "valid": ("2025", "2026")})
    model = Model()
    selected = _selected_training_steps(model, "lightgbm", {})

    _fit_final_model(model, dataset, "lightgbm", selected)

    assert selected == 37
    assert calls == [({"train": ("2020", "2026")}, {"num_boost_round": 37, "early_stopping_rounds": 0})]
