from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import lightgbm as lgb
import numpy as np
import pandas as pd

from tushare_qlib.model_bundle import create_model_bundle, load_model_bundle, verify_model_bundle
from tushare_qlib.settings import Paths, Settings


class RobustZScoreNorm:
    def __init__(self, width: int):
        self.mean_train = np.zeros(width)
        self.std_train = np.ones(width)


class _Handler:
    def __init__(self, columns: list[str]):
        self.columns = columns
        self.infer_processors = [RobustZScoreNorm(len(columns))]

    def get_cols(self, col_set: str):
        assert col_set == "feature"
        return self.columns


class _Dataset:
    def __init__(self, features: pd.DataFrame):
        self.features = features
        self.handler = _Handler(list(features.columns))

    def prepare(self, segment: str, col_set: str, data_key: str):
        assert segment == "test"
        assert col_set == "feature"
        return self.features


def test_lightgbm_bundle_round_trip_and_checksum(tmp_path: Path):
    index = pd.MultiIndex.from_product(
        [[pd.Timestamp("2026-08-10")], ["SH600000", "SZ000001", "SH600519"]],
        names=["datetime", "instrument"],
    )
    features = pd.DataFrame([[0.0, 1.0], [1.0, 0.0], [0.5, 0.5]], index=index, columns=["F0", "F1"])
    training = lgb.Dataset(features.to_numpy(), label=np.array([0.1, 0.9, 0.4]))
    booster = lgb.train(
        {"objective": "regression", "verbosity": -1, "min_data_in_leaf": 1},
        training,
        num_boost_round=3,
    )
    settings = Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={},
        paths=Paths.from_root(tmp_path / "data"),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib",
    )
    settings.paths.mkdirs()
    path = create_model_bundle(
        settings,
        model=SimpleNamespace(model=booster),
        dataset=_Dataset(features),
        family="lightgbm",
        model_parameters={"objective": "regression"},
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

    manifest = verify_model_bundle(path.parent)
    loaded = load_model_bundle(path.parent)
    assert manifest["modelFamily"] == "lightgbm"
    np.testing.assert_allclose(
        loaded.predict(features).to_numpy(), booster.predict(features.to_numpy()), rtol=1e-6
    )


def test_ridge_bundle_round_trip_is_deterministic(tmp_path: Path):
    index = pd.MultiIndex.from_product(
        [[pd.Timestamp("2026-08-10")], ["SH600000", "SZ000001", "SH600519"]],
        names=["datetime", "instrument"],
    )
    features = pd.DataFrame([[0.0, 1.0], [1.0, 0.0], [0.5, 0.5]], index=index, columns=["F0", "F1"])
    model = SimpleNamespace(coef_=np.asarray([0.25, -0.5]), intercept_=0.1)
    settings = Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={},
        paths=Paths.from_root(tmp_path / "data"),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib",
    )
    settings.paths.mkdirs()
    path = create_model_bundle(
        settings,
        model=model,
        dataset=_Dataset(features),
        family="ridge",
        model_parameters={"estimator": "ridge", "alpha": 1.0},
        canonical_config={"dataset": {}, "strategy": {}},
        research_run_id="research-ridge-1",
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
    expected = features.to_numpy() @ model.coef_ + model.intercept_
    assert loaded.manifest["modelFamily"] == "ridge"
    np.testing.assert_array_equal(loaded.predict(features).to_numpy(), expected)


def test_xgboost_bundle_round_trip_when_dependency_is_available(tmp_path: Path):
    import pytest

    xgb = pytest.importorskip("xgboost")
    index = pd.MultiIndex.from_product(
        [[pd.Timestamp("2026-08-10")], ["SH600000", "SZ000001", "SH600519"]],
        names=["datetime", "instrument"],
    )
    features = pd.DataFrame([[0.0, 1.0], [1.0, 0.0], [0.5, 0.5]], index=index, columns=["F0", "F1"])
    booster = xgb.train(
        {"objective": "reg:squarederror", "seed": 42},
        xgb.DMatrix(features, label=np.asarray([0.1, 0.9, 0.4])),
        num_boost_round=3,
    )
    settings = Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={},
        paths=Paths.from_root(tmp_path / "data"),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib",
    )
    settings.paths.mkdirs()
    path = create_model_bundle(
        settings,
        model=SimpleNamespace(model=booster),
        dataset=_Dataset(features),
        family="xgboost",
        model_parameters={"objective": "reg:squarederror"},
        canonical_config={"dataset": {}, "strategy": {}},
        research_run_id="research-xgboost-1",
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
    np.testing.assert_allclose(
        loaded.predict(features).to_numpy(),
        booster.predict(xgb.DMatrix(features)),
        rtol=1e-6,
    )


def test_dnn_bundle_round_trip_when_torch_is_available(tmp_path: Path):
    import pytest

    if importlib.util.find_spec("torch") is None:
        pytest.skip("could not import 'torch': No module named 'torch'")
    probe = Path(__file__).with_name("helpers") / "dnn_bundle_roundtrip.py"
    completed = subprocess.run(
        [sys.executable, str(probe), str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
