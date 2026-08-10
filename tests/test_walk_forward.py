import pandas as pd
import pytest

from tushare_qlib.settings import Paths, Settings
from tushare_qlib.walk_forward import (
    Fold,
    _aggregate_component_timings,
    _checkpoint_fingerprint,
    _rebase_reports,
    build_walk_forward_plan,
)


def test_walk_forward_plan_has_non_overlapping_oos_and_final_holdout():
    calendar = pd.bdate_range("2016-01-01", "2026-01-31")

    folds = build_walk_forward_plan(calendar, "2016-01-01", "2026-01-31")

    assert folds[-1].final_holdout is True
    assert len(folds) > 2
    for previous, current in zip(folds, folds[1:], strict=False):
        assert pd.Timestamp(previous.test[1]) < pd.Timestamp(current.test[0])
        assert pd.Timestamp(current.train[1]) < pd.Timestamp(current.valid[0])
        assert pd.Timestamp(current.valid[1]) < pd.Timestamp(current.test[0])


def test_rebase_reports_chains_account_across_fold_resets():
    first = pd.DataFrame(
        {
            "account": [100.0, 110.0],
            "return": [0.0, 0.1],
            "cash": [100.0, 10.0],
            "value": [0.0, 100.0],
            "total_cost": [0.0, 1.0],
            "total_turnover": [0.0, 100.0],
        },
        index=pd.to_datetime(["2026-01-05", "2026-01-06"]),
    )
    second = pd.DataFrame(
        {
            "account": [100.0, 105.0],
            "return": [0.0, 0.05],
            "cash": [100.0, 5.0],
            "value": [0.0, 100.0],
            "total_cost": [0.0, 2.0],
            "total_turnover": [0.0, 120.0],
        },
        index=pd.to_datetime(["2026-01-07", "2026-01-08"]),
    )

    combined = _rebase_reports([("rolling_00", first), ("final_holdout", second)])

    assert combined["account"].tolist() == pytest.approx([100.0, 110.0, 110.0, 115.5])
    assert combined["fold_key"].tolist() == ["rolling_00", "rolling_00", "final_holdout", "final_holdout"]


def test_aggregate_component_timings_sums_each_phase():
    manifests = [
        {"timings": {"phasesSeconds": {"train_seconds": 2.5, "backtest_seconds": 1.0}}},
        {"timings": {"phasesSeconds": {"train_seconds": 3.5, "predict_seconds": 0.5}}},
    ]

    assert _aggregate_component_timings(manifests) == {
        "train_seconds": 6.0,
        "backtest_seconds": 1.0,
        "predict_seconds": 0.5,
    }


def test_checkpoint_fingerprint_changes_with_profile_or_fold(tmp_path):
    settings = Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={"research": {"random_seed": 42}, "universe": {"instruments": "all"}},
        paths=Paths.from_root(tmp_path / "data"),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib",
    )
    fold = Fold("rolling_00", ("2020-01-01", "2021-01-01"), ("2021-02-01", "2021-03-01"), ("2021-04-01", "2021-05-01"))

    base = _checkpoint_fingerprint(settings, fold, runtime_fingerprint="cpu", benchmark="SH000300", topn=30)
    other_device = _checkpoint_fingerprint(
        settings, fold, runtime_fingerprint="cuda", benchmark="SH000300", topn=30
    )
    changed_fold = Fold("rolling_00", fold.train, fold.valid, ("2021-04-01", "2021-06-01"))
    other_fold = _checkpoint_fingerprint(
        settings, changed_fold, runtime_fingerprint="cpu", benchmark="SH000300", topn=30
    )

    assert base != other_device
    assert base != other_fold
