from __future__ import annotations

from pathlib import Path

import pytest

from qlib_platform.canonical_config import StrategySpec
from qlib_platform.settings import Paths, Settings
from qlib_platform.backtesting.strategy_factory import (
    build_qlib_strategy_config,
    resolve_strategy_policy,
    strategy_policy_id,
)
from qlib_platform.backtesting.topk_dropout import RankBufferPolicy, TopkDropoutPolicy


def _settings(tmp_path: Path, data: dict[str, object]) -> Settings:
    return Settings(
        config_path=tmp_path / "pipeline.yaml",
        data=data,
        paths=Paths.from_root(tmp_path / "data"),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib",
    )


def test_resolve_strategy_policy_round_trips_policy_objects() -> None:
    topk = TopkDropoutPolicy(topk=10, n_drop=3, hold_thresh=1)
    rank = RankBufferPolicy(target_size=10, entry_rank=10, exit_rank=20)

    assert resolve_strategy_policy(topk) is topk
    assert resolve_strategy_policy(rank) is rank


def test_resolve_strategy_policy_dispatches_on_strategy_spec(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        {
            "strategy": {
                "policy": "rank_buffer_v1",
                "rank_buffer": {
                    "target_size": 10,
                    "entry_rank": 10,
                    "exit_rank": 20,
                    "max_replacements": 3,
                    "hold_thresh": 1,
                },
            },
        },
    )
    spec = StrategySpec.from_settings(settings)

    policy = resolve_strategy_policy(spec)

    assert isinstance(policy, RankBufferPolicy)
    assert policy.target_size == 10
    assert policy.entry_rank == 10
    assert policy.exit_rank == 20
    assert strategy_policy_id(policy) == "rank_buffer_v1"


def test_resolve_strategy_policy_rejects_unknown_policy_type() -> None:
    with pytest.raises(TypeError, match="cannot resolve"):
        resolve_strategy_policy(object())  # type: ignore[arg-type]


def test_build_qlib_strategy_config_routes_topk_through_ashare_adapter() -> None:
    policy = TopkDropoutPolicy(topk=10, n_drop=3, hold_thresh=1, risk_degree=0.95)

    config = build_qlib_strategy_config(policy)

    assert config["class"] == "AShareTopkDropoutStrategy"
    assert config["module_path"] == "qlib_platform.backtesting.qlib_strategies"
    kwargs = config["kwargs"]
    assert kwargs["signal"] == "<PRED>"
    assert kwargs["topk"] == 10
    assert kwargs["n_drop"] == 3
    assert kwargs["hold_thresh"] == 1
    assert kwargs["risk_degree"] == 0.95


def test_build_qlib_strategy_config_routes_rank_buffer_through_ashare_adapter() -> None:
    policy = RankBufferPolicy(
        target_size=10,
        entry_rank=10,
        exit_rank=20,
        max_replacements=3,
        hold_thresh=1,
        risk_degree=0.95,
    )

    config = build_qlib_strategy_config(policy)

    assert config["class"] == "AShareRankBufferStrategy"
    assert config["module_path"] == "qlib_platform.backtesting.qlib_strategies"
    kwargs = config["kwargs"]
    assert kwargs["signal"] == "<PRED>"
    assert kwargs["target_size"] == 10
    assert kwargs["entry_rank"] == 10
    assert kwargs["exit_rank"] == 20
    assert kwargs["max_replacements"] == 3
    assert kwargs["hold_thresh"] == 1
    assert kwargs["risk_degree"] == 0.95


def test_build_qlib_strategy_config_rejects_t0_policy() -> None:
    policy = TopkDropoutPolicy(topk=10, n_drop=3, hold_thresh=0)

    with pytest.raises(ValueError, match="hold_thresh >= 1"):
        build_qlib_strategy_config(policy)


def test_rank_buffer_policy_from_mapping_accepts_config_camel_case() -> None:
    policy = RankBufferPolicy.from_mapping(
        {
            "targetSize": 10,
            "entryRank": 10,
            "exitRank": 20,
            "maxReplacements": 3,
            "holdThresholdSessions": 1,
            "onlyTradable": True,
            "forbidAllTradeAtLimit": True,
            "riskDegree": 0.95,
        }
    )

    assert policy.target_size == 10
    assert policy.entry_rank == 10
    assert policy.exit_rank == 20
    assert policy.max_replacements == 3
    assert policy.hold_thresh == 1
    assert policy.risk_degree == 0.95
    policy.validate()


def test_rank_buffer_policy_validation_rejects_invalid_combinations() -> None:
    with pytest.raises(ValueError, match="target_size"):
        RankBufferPolicy(target_size=0).validate()
    with pytest.raises(ValueError, match="0 < entry_rank < exit_rank"):
        RankBufferPolicy(entry_rank=20, exit_rank=20).validate()
    with pytest.raises(ValueError, match="risk_degree"):
        RankBufferPolicy(risk_degree=1.5).validate()
