from __future__ import annotations

import pandas as pd
import pytest

from qlib_platform.canonical_config import StrategySpec
from qlib_platform.backtesting.prediction_backtest import (
    _load_predictions,
    _portfolio_config,
    backtest_predictions,
)
from qlib_platform.artifacts.prediction_snapshot import (
    PredictionSnapshotSpec,
    load_prediction_snapshot,
    write_prediction_snapshot,
)
from qlib_platform.settings import Paths, Settings
from qlib_platform.backtesting.topk_dropout import TopkDropoutPolicy


def test_load_predictions_normalizes_single_score_column(tmp_path):
    index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2026-01-05"), "SH600000")],
        names=["datetime", "instrument"],
    )
    source = tmp_path / "pred.parquet"
    pd.DataFrame({"prediction": [0.25]}, index=index).to_parquet(source)

    loaded = _load_predictions(source)

    assert list(loaded.columns) == ["score"]
    assert loaded.iloc[0, 0] == pytest.approx(0.25)


def test_load_predictions_rejects_non_qlib_index(tmp_path):
    source = tmp_path / "pred.parquet"
    pd.DataFrame({"score": [0.25]}).to_parquet(source)

    with pytest.raises(ValueError, match="datetime/instrument MultiIndex"):
        _load_predictions(source)


def test_predictions_backtest_input_can_be_checksum_verified_snapshot(tmp_path):
    index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2026-01-05"), "SH600000")],
        names=["datetime", "instrument"],
    )
    source = tmp_path / "pred.parquet"
    write_prediction_snapshot(
        source,
        pd.DataFrame({"score": [0.25]}, index=index),
        spec=PredictionSnapshotSpec(
            data_release_id="ds_test",
            alpha_pack_id="alpha158_pit_v1",
            feature_snapshot_id="fs_test",
            label_spec_id="return_5d_t1_v1",
            split_spec_id="split_test",
            model_id="model_test",
            model_profile_id="ridge_golden_v1",
            fold_id="fold_1",
        ),
    )

    loaded, manifest = load_prediction_snapshot(source)

    assert loaded.iloc[0]["score"] == pytest.approx(0.25)
    assert manifest["artifactType"] == "PREDICTION_SNAPSHOT"


@pytest.mark.parametrize(
    ("configured_release", "snapshot_release", "materialized_release"),
    [
        ("ds_B", "ds_A", "ds_B"),
        ("ds_A", "ds_A", "ds_B"),
    ],
)
def test_prediction_snapshot_rejects_wrong_pinned_data_release_before_side_effects(
    tmp_path,
    monkeypatch,
    configured_release,
    snapshot_release,
    materialized_release,
):
    qlib_data = tmp_path / "qlib"
    qlib_data.mkdir()
    (qlib_data / "dataset_manifest.json").write_text(
        f"""{{
          "schema_version": "3.0",
          "version_id": "qlib_content_b",
          "dataset_name": "test",
          "semantic_contract": {{"data_release_id": "{materialized_release}"}}
        }}""",
        encoding="utf-8",
    )
    settings = Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={
            "data_source": {
                "kind": "platform_release",
                "platform_release": {"id": configured_release},
            },
            "qlib": {"dataset_version": configured_release},
        },
        paths=Paths.from_root(tmp_path / "data"),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=qlib_data,
    )
    index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2026-01-05"), "SH600000")],
        names=["datetime", "instrument"],
    )
    source = tmp_path / "pred.parquet"
    write_prediction_snapshot(
        source,
        pd.DataFrame({"score": [0.25]}, index=index),
        spec=PredictionSnapshotSpec(
            data_release_id=snapshot_release,
            alpha_pack_id="alpha158_pit_v1",
            feature_snapshot_id="fs_test",
            label_spec_id="return_5d_t1_v1",
            split_spec_id="split_test",
            model_id="model_test",
            model_profile_id="ridge_golden_v1",
            fold_id="fold_1",
        ),
    )
    qlib_init_calls: list[object] = []
    monkeypatch.setattr("qlib.init", lambda *args, **kwargs: qlib_init_calls.append((args, kwargs)))

    with pytest.raises(
        ValueError,
        match="PredictionSnapshot DataRelease does not match pinned DataRelease",
    ):
        backtest_predictions(settings, source)

    assert qlib_init_calls == []
    assert not settings.paths.output.exists()
    assert not (tmp_path / "mlruns").exists()


def test_portfolio_config_uses_strategy_and_execution_settings(tmp_path):
    settings = Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={
            "research": {
                "backtest_account": 123_000,
                "deal_price": "open",
                "open_cost": 0.001,
                "close_cost": 0.002,
            }
        },
        paths=Paths.from_root(tmp_path / "data"),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib",
    )
    policy = TopkDropoutPolicy(topk=20, n_drop=3, hold_thresh=4)

    config = _portfolio_config(
        settings,
        policy=policy,
        benchmark="benchmark",
        start_time="2026-01-01",
        end_time="2026-01-31",
    )

    assert config["strategy"]["kwargs"]["topk"] == 20
    assert config["backtest"]["account"] == 123_000
    assert config["backtest"]["exchange_kwargs"]["open_cost"] == pytest.approx(0.001)


def test_strategy_overrides_support_prediction_only_portfolio_matrix(tmp_path):
    settings = Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={
            "strategy": {
                "topk_dropout": {"topk": 30, "n_drop": 5, "hold_thresh": 5},
            }
        },
        paths=Paths.from_root(tmp_path / "data"),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib",
    )

    strategy = StrategySpec.from_settings(
        settings,
        topk_override=50,
        n_drop_override=10,
        hold_thresh_override=7,
    )

    assert strategy.topk == 50
    assert strategy.n_drop == 10
    assert strategy.hold_thresh == 7
