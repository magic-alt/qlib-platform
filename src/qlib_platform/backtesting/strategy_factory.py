from __future__ import annotations

from dataclasses import asdict
from typing import TypeAlias

from qlib_platform.backtesting.topk_dropout import RankBufferPolicy, TopkDropoutPolicy

StrategyPolicy: TypeAlias = TopkDropoutPolicy | RankBufferPolicy


def resolve_strategy_policy(spec: object) -> StrategyPolicy:
    """Resolve the execution policy from a StrategySpec (or a policy directly).

    Accepting an already-resolved policy keeps the factory callable from both
    the canonical config path and prediction-only backtests.
    """
    if isinstance(spec, (TopkDropoutPolicy, RankBufferPolicy)):
        return spec
    to_policy = getattr(spec, "to_policy", None)
    if to_policy is None:
        raise TypeError(f"cannot resolve a strategy policy from {type(spec).__name__}")
    policy = to_policy()
    if not isinstance(policy, (TopkDropoutPolicy, RankBufferPolicy)):
        raise TypeError(f"to_policy() returned an unsupported policy: {type(policy).__name__}")
    return policy


def build_qlib_strategy_config(policy: StrategyPolicy, *, signal: str = "<PRED>") -> dict[str, object]:
    """Build the Qlib ``PortAnaRecord`` strategy block for an execution policy.

    The rank buffer runs through the formal ``RankBufferStrategy`` in this
    repository so that the backtest, the decision replay and the audit share
    one implementation.  TopkDropout keeps the Qlib-native strategy class.
    """
    if isinstance(policy, RankBufferPolicy):
        policy.validate()
        return {
            "class": "RankBufferStrategy",
            "module_path": "qlib_platform.backtesting.qlib_strategies",
            "kwargs": {"signal": signal, **asdict(policy)},
        }
    policy.validate()
    return {
        "class": "TopkDropoutStrategy",
        "module_path": "qlib.contrib.strategy",
        "kwargs": {"signal": signal, **asdict(policy)},
    }


def strategy_policy_id(policy: StrategyPolicy) -> str:
    if isinstance(policy, RankBufferPolicy):
        return "rank_buffer_v1"
    return "topk_dropout_v1"
