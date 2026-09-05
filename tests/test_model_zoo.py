from __future__ import annotations

from types import SimpleNamespace

import pytest

from qlib_platform.models.adapters.qlib_zoo import _TemporalQlibModel
from qlib_platform.models.registry import get_model_adapter, model_families


def _profile(**kwargs):
    defaults = {
        "device": "cpu",
        "device_index": 0,
        "gpu_platform_id": 0,
        "model_kwargs": {},
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_qlib_model_zoo_is_registered() -> None:
    expected = {
        "qlib_lstm",
        "qlib_gru",
        "qlib_transformer",
        "qlib_tcn",
        "qlib_tabnet",
        "qlib_double_ensemble",
    }
    assert expected.issubset(set(model_families()))


@pytest.mark.parametrize(
    "family",
    ["qlib_lstm", "qlib_gru", "qlib_transformer", "qlib_tcn"],
)
def test_temporal_model_parameters_bind_same_feature_width_and_sequence_window(family: str) -> None:
    adapter = get_model_adapter(family)
    params = adapter.parameters(
        _profile(model_kwargs={"step_len": 20}),
        "cpu",
        feature_count=183,
        seed=17,
        num_threads=4,
    )
    assert params["d_feat"] == 183
    assert params["_qlib_platform_step_len"] == 20
    assert params["GPU"] == -1
    assert params["seed"] == 17
    assert params["n_jobs"] == 4
    assert adapter.deployment_capable is False


def test_temporal_model_rejects_per_model_feature_width_drift() -> None:
    adapter = get_model_adapter("qlib_lstm")
    with pytest.raises(ValueError, match="fair-comparison"):
        adapter.parameters(
            _profile(model_kwargs={"d_feat": 20, "step_len": 20}),
            "cpu",
            feature_count=183,
            seed=17,
            num_threads=4,
        )


def test_tabnet_does_not_inject_unsupported_n_jobs() -> None:
    adapter = get_model_adapter("qlib_tabnet")
    params = adapter.parameters(
        _profile(),
        "cpu",
        feature_count=158,
        seed=1,
        num_threads=8,
    )
    assert params["d_feat"] == 158
    assert "n_jobs" not in params


def test_research_only_zoo_fails_closed_for_model_bundle() -> None:
    adapter = get_model_adapter("qlib_double_ensemble")
    with pytest.raises(RuntimeError, match="research-only"):
        adapter.save(object(), None)  # type: ignore[arg-type]


def test_temporal_wrapper_preserves_handler_and_segments(monkeypatch) -> None:
    from types import SimpleNamespace

    captured = {}

    class FakeTSDatasetH:
        def __init__(self, *, handler, segments, step_len):
            captured.update(handler=handler, segments=segments, step_len=step_len)

    import qlib.data.dataset

    monkeypatch.setattr(qlib.data.dataset, "TSDatasetH", FakeTSDatasetH)
    handler = object()
    segments = {
        "train": ("2020-01-01", "2022-12-31"),
        "valid": ("2023-01-01", "2023-12-31"),
        "test": ("2024-01-01", "2024-12-31"),
    }
    wrapper = _TemporalQlibModel(model=object(), step_len=20)
    wrapper._dataset(SimpleNamespace(handler=handler, segments=segments))
    assert captured == {"handler": handler, "segments": segments, "step_len": 20}
