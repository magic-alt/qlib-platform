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
    """Build a Qlib strategy block with the cash A-share execution guard.

    Both supported policies run through local adapters so that the exact Qlib
    Exchange instance shared with the executor receives the same T+1 and
    board-size legality contract as the standalone A-share simulator.
    """

    policy.validate()
    if policy.hold_thresh < 1:
        raise ValueError("A-share cash strategy requires hold_thresh >= 1 for T+1")
    return {
        "class": (
            "AShareRankBufferStrategy"
            if isinstance(policy, RankBufferPolicy)
            else "AShareTopkDropoutStrategy"
        ),
        "module_path": "qlib_platform.backtesting.qlib_strategies",
        "kwargs": {"signal": signal, **asdict(policy)},
    }


def strategy_policy_id(policy: StrategyPolicy) -> str:
    if isinstance(policy, RankBufferPolicy):
        return "rank_buffer_v1"
    return "topk_dropout_v1"
