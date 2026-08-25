from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml


ROOT = Path(__file__).parents[1]
EXAMPLE_ROOT = ROOT / "examples" / "local_qlib_backtest"
WORKFLOWS = (
    EXAMPLE_ROOT / "workflow_lightgbm.yaml",
    EXAMPLE_ROOT / "workflow_ridge.yaml",
    EXAMPLE_ROOT / "workflow_custom_ridge.yaml",
)


def _load_workflow(path: Path) -> dict[str, object]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_local_backtest_workflows_keep_research_and_execution_contract_fixed() -> None:
    configs = [_load_workflow(path) for path in WORKFLOWS]
    reference = configs[0]

    for config in configs:
        assert config["qlib_init"]["provider_uri"] == "{{ QLIB_DATA_URI }}"
        assert config["market"] == "csi300"
        assert config["benchmark"] == "SH000300"
        assert config["data_handler_config"] == reference["data_handler_config"]
        assert config["task"]["dataset"] == reference["task"]["dataset"]
        assert config["task"]["record"] == reference["task"]["record"]

        segments = config["task"]["dataset"]["kwargs"]["segments"]
        assert segments["train"][1] < segments["valid"][0]
        assert segments["valid"][1] < segments["test"][0]


def test_custom_ridge_uses_train_only_and_preserves_prediction_index() -> None:
    spec = importlib.util.spec_from_file_location(
        "local_example_custom_model", EXAMPLE_ROOT / "custom_model.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    columns = pd.Index(["f0", "f1"])
    train_index = pd.MultiIndex.from_product(
        [[pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03")], ["SH600000", "SZ000001"]],
        names=["datetime", "instrument"],
    )
    test_index = pd.MultiIndex.from_product(
        [[pd.Timestamp("2025-01-02")], ["SH600000", "SZ000001"]],
        names=["datetime", "instrument"],
    )
    train = pd.concat(
        {
            "feature": pd.DataFrame(
                [[0.0, 1.0], [1.0, 0.0], [2.0, 1.0], [3.0, 0.0]],
                index=train_index,
                columns=columns,
            ),
            "label": pd.DataFrame([[0.0], [0.2], [0.8], [1.0]], index=train_index, columns=["LABEL0"]),
        },
        axis=1,
    )
    test = pd.DataFrame([[1.5, 0.5], [2.5, 0.5]], index=test_index, columns=columns)

    class DatasetStub:
        requested_segments: list[str] = []

        def prepare(self, segment: str, **_: object) -> pd.DataFrame:
            self.requested_segments.append(segment)
            return train if segment == "train" else test

    dataset = DatasetStub()
    model = module.WinsorizedRidgeModel(alpha=1.0, clip_quantile=0.1)
    model.fit(dataset)
    prediction = model.predict(dataset)

    assert dataset.requested_segments == ["train", "test"]
    assert prediction.index.equals(test_index)
    assert np.isfinite(prediction).all()
    assert prediction.between(model.lower_bound, model.upper_bound).all()


@pytest.mark.parametrize("clip_quantile", [-0.01, 0.5, 1.0])
def test_custom_ridge_rejects_invalid_clip_quantile(clip_quantile: float) -> None:
    spec = importlib.util.spec_from_file_location(
        "local_example_custom_model_invalid", EXAMPLE_ROOT / "custom_model.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with pytest.raises(ValueError, match="clip_quantile"):
        module.WinsorizedRidgeModel(clip_quantile=clip_quantile)
