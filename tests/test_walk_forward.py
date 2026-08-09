import pandas as pd
import pytest

from tushare_qlib.walk_forward import _rebase_reports, build_walk_forward_plan


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
