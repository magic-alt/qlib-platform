from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import torch
from qlib.contrib.model.pytorch_nn import Net

from qlib_platform.models.model_bundle import create_model_bundle, load_model_bundle
from qlib_platform.settings import Paths, Settings


class RobustZScoreNorm:
    def __init__(self, width: int) -> None:
        self.mean_train = np.zeros(width)
        self.std_train = np.ones(width)


class _Handler:
    def __init__(self, columns: list[str]) -> None:
        self.columns = columns
        self.infer_processors = [RobustZScoreNorm(len(columns))]

    def get_cols(self, col_set: str) -> list[str]:
        if col_set != "feature":
            raise ValueError(f"unexpected column set: {col_set}")
        return self.columns


class _Dataset:
    def __init__(self, features: pd.DataFrame) -> None:
        self.features = features
        self.handler = _Handler(list(features.columns))

    def prepare(self, segment: str, col_set: str, data_key: str) -> pd.DataFrame:
        if segment != "test" or col_set != "feature" or not data_key:
            raise ValueError("unexpected parity dataset request")
        return self.features


def main(root: Path) -> None:
    index = pd.MultiIndex.from_product(
        [[pd.Timestamp("2026-08-10")], ["SH600000", "SZ000001"]],
        names=["datetime", "instrument"],
    )
    features = pd.DataFrame([[0.0, 1.0], [1.0, 0.0]], index=index, columns=["F0", "F1"])
    wrapper = SimpleNamespace(
        dnn_model=Net(input_dim=2, layers=[4]),
        fitted=True,
        device=torch.device("cpu"),
    )
    settings = Settings(
        config_path=root / "pipeline.yaml",
        data={},
        paths=Paths.from_root(root / "data"),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=root / "qlib",
    )
    settings.paths.mkdirs()
    path = create_model_bundle(
        settings,
        model=wrapper,
        dataset=_Dataset(features),
        family="pytorch_dnn",
        model_parameters={"pt_model_kwargs": {"input_dim": 2, "layers": [4]}},
        canonical_config={"dataset": {}, "strategy": {}},
        research_run_id="research-1",
        refit_as_of="2026-08-10",
        train_window=("2020-01-01", "2025-01-01"),
        valid_window=("2025-02-01", "2026-01-01"),
        dataset_id="dataset-1",
        dataset_sha256="dataset-sha",
        feature_store=None,
        lineage={"complete": True},
        seed=42,
    )
    loaded = load_model_bundle(path.parent)
    expected = (
        wrapper.dnn_model(torch.from_numpy(features.to_numpy(dtype=np.float32))).detach().numpy().reshape(-1)
    )
    np.testing.assert_allclose(loaded.predict(features).to_numpy(), expected, rtol=1e-5)


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
