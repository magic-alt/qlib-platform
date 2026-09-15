from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from qlib_platform.backtesting.portfolio import PortfolioPolicy
from qlib_platform.backtesting.strategy_factory import (
    build_qlib_strategy_config,
    strategy_descriptor,
)
from qlib_platform.backtesting.strategy_sdk import (
    BackendContract,
    BaselineStrategy,
    EvaluationProtocol,
    PortfolioWeightingPolicy,
    RebalancePolicy,
    ResearchCostModel,
    ScoreThresholdPolicy,
    ShadowStrategy,
    apply_score_threshold_shadow,
    backend_contract,
    describe_strategy,
    get_strategy_registration,
    strategy_registry,
)
from qlib_platform.backtesting.topk_dropout import (
    RankBufferPolicy,
    TopkDropoutPolicy,
    rank_buffer_decision,
    topk_dropout_decision,
)


FIXTURES = Path(__file__).parent / "fixtures" / "strategy_baselines.json"


def _quotes(instruments: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "instrument": instruments,
            "paused": [0] * len(instruments),
            "is_limit_up": [0] * len(instruments),
            "is_limit_down": [0] * len(instruments),
        }
    )


def _goldens() -> dict[str, object]:
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


def test_strategy_registry_freezes_baselines_and_shadow_family() -> None:
    registry = strategy_registry()

    topk = registry["topk_dropout_v1"]
    rank = registry["rank_buffer_v1"]
    shadow = registry["score_threshold_shadow_v1"]

    assert isinstance(topk, BaselineStrategy)
    assert isinstance(rank, BaselineStrategy)
    assert isinstance(shadow, ShadowStrategy)
    assert topk.qlib_class == "AShareTopkDropoutStrategy"
    assert rank.qlib_class == "AShareRankBufferStrategy"
    assert shadow.baseline_strategy_id == "topk_dropout_v1"

    copy = strategy_registry()
    del copy["topk_dropout_v1"]
    assert "topk_dropout_v1" in strategy_registry()

    with pytest.raises(ValueError, match="unknown strategy SDK id"):
        get_strategy_registration("missing")


def test_baseline_descriptor_composes_portfolio_rebalance_cost_and_backends() -> None:
    policy = TopkDropoutPolicy(topk=20, n_drop=4, hold_thresh=5)
    descriptor = strategy_descriptor(
        policy,
        portfolio_policy=PortfolioPolicy(weighting="score", max_turnover=0.30),
        weighting_policy=PortfolioWeightingPolicy(method="rank"),
        rebalance_policy=RebalancePolicy(max_turnover=0.15),
        cost_model=ResearchCostModel(
            model_id="explicit-research-assumption",
            expected_cost_bps=8.0,
            source="test-only-assumption",
        ),
    )

    assert descriptor["strategyId"] == "topk_dropout_v1"
    assert descriptor["registration"]["mode"] == "baseline"
    assert descriptor["decisionPolicy"]["topk"] == 20
    assert descriptor["portfolioPolicy"]["weighting"] == "rank"
    assert descriptor["portfolioPolicy"]["max_turnover"] == 0.15
    assert descriptor["researchCostModel"]["calibrated"] is True
    assert descriptor["evaluationProtocol"] == {
        "mode": "baseline",
        "formal_candidate": False,
        "search_enabled": False,
    }
    assert descriptor["backendContract"]["qlib_mode"] == "native_strategy_adapter"
    assert descriptor["backendContract"]["quant_mode"] == "target_portfolio_v1"


def test_shadow_descriptor_is_evaluation_only_and_never_a_candidate() -> None:
    descriptor = describe_strategy("score_threshold_shadow_v1")

    assert descriptor["registration"]["mode"] == "shadow"
    assert descriptor["evaluationProtocol"]["formal_candidate"] is False
    assert descriptor["evaluationProtocol"]["search_enabled"] is False
    assert descriptor["backendContract"] == {
        "qlib_mode": "evaluation_only",
        "quant_mode": "target_portfolio_v1",
        "qlib_class": None,
    }
    assert descriptor["researchCostModel"]["calibrated"] is False


def test_backend_contract_matches_real_qlib_adapter_for_each_baseline() -> None:
    for policy in (
        TopkDropoutPolicy(topk=10, n_drop=3, hold_thresh=1),
        RankBufferPolicy(
            target_size=10,
            entry_rank=10,
            exit_rank=20,
            max_replacements=3,
            hold_thresh=1,
        ),
    ):
        descriptor = strategy_descriptor(policy)
        qlib = build_qlib_strategy_config(policy)
        assert descriptor["backendContract"]["qlib_class"] == qlib["class"]
        assert descriptor["backendContract"]["quant_mode"] == "target_portfolio_v1"

    shadow = backend_contract(get_strategy_registration("score_threshold_shadow_v1"))
    assert shadow.qlib_mode == "evaluation_only"
    assert shadow.qlib_class is None


def test_policy_contract_validation_is_fail_closed() -> None:
    with pytest.raises(ValueError, match="weighting"):
        PortfolioWeightingPolicy("optimizer").validate()
    with pytest.raises(ValueError, match="daily"):
        RebalancePolicy(frequency="weekly").validate()
    with pytest.raises(ValueError, match="max_turnover"):
        RebalancePolicy(max_turnover=3.0).validate()
    with pytest.raises(ValueError, match="non-negative"):
        ResearchCostModel(expected_cost_bps=-1.0).validate()
    with pytest.raises(ValueError, match="diagnosis-only"):
        EvaluationProtocol(mode="shadow", formal_candidate=True).validate()
    with pytest.raises(ValueError, match="unsupported evaluation mode"):
        EvaluationProtocol(mode="candidate").validate()
    with pytest.raises(ValueError, match="qlib_class"):
        BackendContract(qlib_mode="native_strategy_adapter").validate()
    with pytest.raises(ValueError, match="cannot declare"):
        BackendContract(qlib_mode="evaluation_only", qlib_class="Forbidden").validate()


def test_score_threshold_policy_runs_only_as_shadow_decision_overlay() -> None:
    decision = pd.DataFrame(
        {
            "instrument": ["A", "B", "C"],
            "score": [0.8, 0.4, 0.2],
            "target_action": ["BUY", "BUY", "SELL"],
        }
    )

    result = apply_score_threshold_shadow(decision, ScoreThresholdPolicy(min_score=0.5))

    assert result["baseline_target_action"].tolist() == ["BUY", "BUY", "SELL"]
    assert result["shadow_target_action"].tolist() == ["BUY", "HOLD", "SELL"]
    assert result["shadow_reason"].tolist() == [
        "BASELINE_UNCHANGED",
        "SCORE_BELOW_THRESHOLD",
        "BASELINE_UNCHANGED",
    ]
    assert result["evaluation_mode"].eq("SHADOW").all()
    assert not result["formal_candidate"].any()
    assert decision["target_action"].tolist() == ["BUY", "BUY", "SELL"]

    exclusive = apply_score_threshold_shadow(
        decision.iloc[[0]],
        ScoreThresholdPolicy(min_score=0.8, inclusive=False),
    )
    assert exclusive.iloc[0]["shadow_target_action"] == "HOLD"

    with pytest.raises(ValueError, match="decision missing columns"):
        apply_score_threshold_shadow(pd.DataFrame({"score": [1.0]}), ScoreThresholdPolicy(0.5))
    with pytest.raises(ValueError, match="finite"):
        ScoreThresholdPolicy(float("nan")).validate()


def test_topk_dropout_v1_matches_frozen_action_fixture() -> None:
    fixture = _goldens()["topk_dropout_v1"]
    scores = pd.Series({"A": 0.9, "B": 0.8, "C": 0.7, "D": 0.6})
    positions = pd.DataFrame(
        {"instrument": ["B", "D"], "quantity": [100, 100], "holding_days": [5, 5]}
    )
    policy = TopkDropoutPolicy(**fixture["policy"])

    decision = topk_dropout_decision(scores, positions, _quotes(list(scores.index)), policy=policy)
    actual = decision[["instrument", "target_action", "action_reason"]].to_dict("records")

    assert actual == fixture["expected"]


def test_rank_buffer_v1_matches_frozen_action_fixture() -> None:
    fixture = _goldens()["rank_buffer_v1"]
    scores = pd.Series({f"S{rank:02d}": 100 - rank for rank in range(1, 26)})
    positions = pd.DataFrame(
        {"instrument": ["S03", "S22"], "quantity": [100, 100], "holding_days": [3, 3]}
    )
    policy = RankBufferPolicy(**fixture["policy"])

    decision = rank_buffer_decision(scores, positions, _quotes(list(scores.index)), policy=policy)
    actions = decision.loc[
        decision["target_action"].isin({"BUY", "SELL"}),
        ["instrument", "target_action", "action_reason"],
    ].to_dict("records")
    held = decision.loc[
        decision["instrument"].eq("S03"),
        ["instrument", "target_action", "action_reason"],
    ].iloc[0].to_dict()

    assert actions == fixture["expected_actions"]
    assert held == fixture["expected_hold"]
