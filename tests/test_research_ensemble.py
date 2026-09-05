from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qlib_platform.research.evaluation.ensemble import (
    DoubleEnsembleConfig,
    average_predictions,
    weighted_blend,
)
from qlib_platform.research.evaluation.stacking import StackingEnsembler


def _oof_fixture() -> tuple[dict[str, pd.Series], pd.Series, pd.DataFrame]:
    index = pd.Index(range(12), name="row")
    labels = pd.Series(np.linspace(-0.03, 0.05, len(index)), index=index)
    predictions = {
        "a": labels * 0.8 + 0.01,
        "b": labels * 1.2 - 0.005,
    }
    fold_id = np.repeat([0, 1, 2, 3], 3)
    prediction_time = pd.to_datetime("2024-01-01") + pd.to_timedelta(fold_id * 10 + 5, unit="D")
    metadata = pd.DataFrame(
        {
            "fold_id": fold_id,
            "is_oof": True,
            "fit_end": prediction_time - pd.Timedelta(days=1),
            "prediction_time": prediction_time,
        },
        index=index,
    )
    return predictions, labels, metadata


def test_average_and_weighted_blend_are_index_stable() -> None:
    predictions, _, _ = _oof_fixture()
    average = average_predictions(predictions)
    blend = weighted_blend(predictions, {"a": 3.0, "b": 1.0})
    assert average.index.equals(predictions["a"].index)
    assert np.allclose(average, (predictions["a"] + predictions["b"]) / 2.0)
    assert np.allclose(blend, predictions["a"] * 0.75 + predictions["b"] * 0.25)


def test_blend_fails_closed_on_population_drift() -> None:
    predictions, _, _ = _oof_fixture()
    predictions["b"] = predictions["b"].iloc[:-1]
    with pytest.raises(ValueError, match="index mismatch"):
        average_predictions(predictions)


def test_stacking_rejects_in_sample_prediction_rows() -> None:
    predictions, labels, metadata = _oof_fixture()
    metadata.loc[3, "is_oof"] = False
    with pytest.raises(ValueError, match="OOF predictions only"):
        StackingEnsembler().fit(predictions, labels, metadata)


def test_stacking_rejects_temporal_leakage() -> None:
    predictions, labels, metadata = _oof_fixture()
    metadata.loc[4, "fit_end"] = metadata.loc[4, "prediction_time"]
    with pytest.raises(ValueError, match="leakage detected"):
        StackingEnsembler().fit(predictions, labels, metadata)


def test_stacking_temporal_cross_fit_uses_earlier_folds_only() -> None:
    predictions, labels, metadata = _oof_fixture()
    stack = StackingEnsembler(ridge_alpha=1e-4).fit(predictions, labels, metadata)
    cross_fitted = stack.temporal_cross_fit_predict(predictions, labels, metadata)
    assert cross_fitted.iloc[:3].isna().all()
    assert cross_fitted.iloc[6:].notna().all()
    future = stack.predict(predictions)
    assert future.notna().all()
    assert stack.diagnostics_ is not None
    assert stack.diagnostics_.n_folds == 4


def test_double_ensemble_config_rejects_uncertified_base_model() -> None:
    with pytest.raises(ValueError, match="base_model='gbm'"):
        DoubleEnsembleConfig(base_model="mlp").validate()
