import pandas as pd

from tushare_qlib.walk_forward import build_walk_forward_plan


def test_walk_forward_plan_has_non_overlapping_oos_and_final_holdout():
    calendar = pd.bdate_range("2016-01-01", "2026-01-31")

    folds = build_walk_forward_plan(calendar, "2016-01-01", "2026-01-31")

    assert folds[-1].final_holdout is True
    assert len(folds) > 2
    for previous, current in zip(folds, folds[1:], strict=False):
        assert pd.Timestamp(previous.test[1]) < pd.Timestamp(current.test[0])
        assert pd.Timestamp(current.train[1]) < pd.Timestamp(current.valid[0])
        assert pd.Timestamp(current.valid[1]) < pd.Timestamp(current.test[0])
