from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Mapping

import pandas as pd

from qlib_platform.backtesting.portfolio import PortfolioPolicy


@dataclass(frozen=True)
class PortfolioWeightingPolicy:
    """Composable portfolio-weighting override for research descriptors."""

    method: str = "inherit"

    def validate(self) -> None:
        if self.method not in {"inherit", "equal", "rank", "score", "score_vol"}:
            raise ValueError(f"unsupported portfolio weighting method: {self.method}")

    def apply(self, policy: PortfolioPolicy) -> PortfolioPolicy:
        self.validate()
        if self.method == "inherit":
            return policy
        return replace(policy, weighting=self.method)


@dataclass(frozen=True)
class RebalancePolicy:
    """Portfolio-level rebalance contract without changing signal semantics."""

    frequency: str = "daily"
    max_turnover: float | None = None

    def validate(self) -> None:
        if self.frequency != "daily":
            raise ValueError("only daily rebalance is currently supported")
        if self.max_turnover is not None and not 0 <= self.max_turnover <= 2:
            raise ValueError("max_turnover must be in [0, 2]")

    def apply(self, policy: PortfolioPolicy) -> PortfolioPolicy:
        self.validate()
        if self.max_turnover is None:
            return policy
        return replace(policy, max_turnover=self.max_turnover)


@dataclass(frozen=True)
class ResearchCostModel:
    """Research-only expected-cost contract.

    ``expected_cost_bps=None`` deliberately means uncalibrated.  The SDK must
    not turn observed execution cost into an invented expected-cost baseline.
    """

    model_id: str = "unconfigured"
    expected_cost_bps: float | None = None
    source: str = "unconfigured"

    def validate(self) -> None:
        if not self.model_id.strip():
            raise ValueError("model_id must be non-empty")
        if self.expected_cost_bps is not None and self.expected_cost_bps < 0:
            raise ValueError("expected_cost_bps must be non-negative")

    @property
    def calibrated(self) -> bool:
        return self.expected_cost_bps is not None


@dataclass(frozen=True)
class EvaluationProtocol:
    """How a strategy family may be evaluated under active research governance."""

    mode: str
    formal_candidate: bool = False
    search_enabled: bool = False

    def validate(self) -> None:
        if self.mode not in {"baseline", "shadow"}:
            raise ValueError(f"unsupported evaluation mode: {self.mode}")
        if self.formal_candidate or self.search_enabled:
            raise ValueError("Strategy SDK evaluation is diagnosis-only under current governance")


@dataclass(frozen=True)
class BackendContract:
    """Explicit boundary between local/Qlib decisions and Quant execution handoff."""

    qlib_mode: str
    quant_mode: str = "target_portfolio_v1"
    qlib_class: str | None = None

    def validate(self) -> None:
        if self.qlib_mode not in {"native_strategy_adapter", "evaluation_only"}:
            raise ValueError(f"unsupported Qlib backend mode: {self.qlib_mode}")
        if self.quant_mode != "target_portfolio_v1":
            raise ValueError("Quant backend contract must use target_portfolio_v1")
        if self.qlib_mode == "native_strategy_adapter" and not self.qlib_class:
            raise ValueError("native Qlib adapters must declare qlib_class")
        if self.qlib_mode == "evaluation_only" and self.qlib_class is not None:
            raise ValueError("evaluation-only policy families cannot declare a Qlib runtime class")


@dataclass(frozen=True)
class BaselineStrategy:
    strategy_id: str
    family: str
    qlib_class: str
    mode: str = "baseline"


@dataclass(frozen=True)
class ShadowStrategy:
    strategy_id: str
    family: str
    baseline_strategy_id: str
    mode: str = "shadow"


StrategyRegistration = BaselineStrategy | ShadowStrategy


_STRATEGY_REGISTRY: dict[str, StrategyRegistration] = {
    "topk_dropout_v1": BaselineStrategy(
        strategy_id="topk_dropout_v1",
        family="topk_dropout",
        qlib_class="AShareTopkDropoutStrategy",
    ),
    "rank_buffer_v1": BaselineStrategy(
        strategy_id="rank_buffer_v1",
        family="rank_buffer",
        qlib_class="AShareRankBufferStrategy",
    ),
    "score_threshold_shadow_v1": ShadowStrategy(
        strategy_id="score_threshold_shadow_v1",
        family="score_threshold",
        baseline_strategy_id="topk_dropout_v1",
    ),
}


def strategy_registry() -> Mapping[str, StrategyRegistration]:
    """Return a defensive copy of the versioned strategy family registry."""

    return dict(_STRATEGY_REGISTRY)


def get_strategy_registration(strategy_id: str) -> StrategyRegistration:
    try:
        return _STRATEGY_REGISTRY[strategy_id]
    except KeyError as exc:
        raise ValueError(f"unknown strategy SDK id: {strategy_id}") from exc


def backend_contract(registration: StrategyRegistration) -> BackendContract:
    if isinstance(registration, BaselineStrategy):
        contract = BackendContract(
            qlib_mode="native_strategy_adapter",
            qlib_class=registration.qlib_class,
        )
    else:
        contract = BackendContract(qlib_mode="evaluation_only")
    contract.validate()
    return contract


def describe_strategy(
    strategy_id: str,
    *,
    decision_policy: Mapping[str, object] | None = None,
    portfolio_policy: PortfolioPolicy | None = None,
    weighting_policy: PortfolioWeightingPolicy | None = None,
    rebalance_policy: RebalancePolicy | None = None,
    cost_model: ResearchCostModel | None = None,
) -> dict[str, object]:
    """Build a portable strategy descriptor without authorizing promotion/search."""

    registration = get_strategy_registration(strategy_id)
    weighting = weighting_policy or PortfolioWeightingPolicy()
    rebalance = rebalance_policy or RebalancePolicy()
    research_cost = cost_model or ResearchCostModel()
    research_cost.validate()
    effective_portfolio = weighting.apply(portfolio_policy or PortfolioPolicy())
    effective_portfolio = rebalance.apply(effective_portfolio)
    effective_portfolio.validate()
    evaluation = EvaluationProtocol(mode=registration.mode)
    evaluation.validate()
    backend = backend_contract(registration)

    registration_payload = asdict(registration)
    cost_payload = {**asdict(research_cost), "calibrated": research_cost.calibrated}
    return {
        "schemaVersion": "1.0",
        "strategyId": strategy_id,
        "registration": registration_payload,
        "decisionPolicy": dict(decision_policy or {}),
        "portfolioPolicy": asdict(effective_portfolio),
        "portfolioWeightingPolicy": asdict(weighting),
        "rebalancePolicy": asdict(rebalance),
        "researchCostModel": cost_payload,
        "evaluationProtocol": asdict(evaluation),
        "backendContract": asdict(backend),
    }


@dataclass(frozen=True)
class ScoreThresholdPolicy:
    """Shadow-only filter that can suppress baseline BUY intent for evaluation."""

    min_score: float
    inclusive: bool = True

    def validate(self) -> None:
        if not pd.notna(self.min_score):
            raise ValueError("min_score must be finite")

    def accepts(self, score: float) -> bool:
        self.validate()
        return score >= self.min_score if self.inclusive else score > self.min_score


def apply_score_threshold_shadow(
    decision: pd.DataFrame,
    policy: ScoreThresholdPolicy,
) -> pd.DataFrame:
    """Evaluate score-threshold behavior without mutating the baseline decision."""

    required = {"instrument", "score", "target_action"}
    missing = required - set(decision.columns)
    if missing:
        raise ValueError(f"decision missing columns: {sorted(missing)}")
    policy.validate()
    result = decision.copy()
    result["baseline_target_action"] = result["target_action"].astype(str)
    score = pd.to_numeric(result["score"], errors="coerce")
    accepted = score.ge(policy.min_score) if policy.inclusive else score.gt(policy.min_score)
    suppressed = result["target_action"].eq("BUY") & ~accepted.fillna(False)
    result["shadow_target_action"] = result["target_action"].astype(str)
    result.loc[suppressed, "shadow_target_action"] = "HOLD"
    result["shadow_reason"] = "BASELINE_UNCHANGED"
    result.loc[suppressed, "shadow_reason"] = "SCORE_BELOW_THRESHOLD"
    result["evaluation_mode"] = "SHADOW"
    result["formal_candidate"] = False
    return result
