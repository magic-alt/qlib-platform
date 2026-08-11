from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class PortfolioPolicy:
    top_n: int = 20
    min_score: float | None = None
    weighting: str = "score_vol"
    max_position: float = 0.08
    max_exposure: float = 0.90
    max_group_exposure: float = 0.25
    max_turnover: float | None = 0.30
    min_position: float = 0.002
    volatility_floor: float = 0.01

    @classmethod
    def from_mapping(cls, data: Mapping[str, object] | None) -> "PortfolioPolicy":
        data = data or {}
        return cls(
            top_n=int(str(data.get("top_n", cls.top_n))),
            min_score=float(str(data["min_score"])) if data.get("min_score") is not None else None,
            weighting=str(data.get("weighting", cls.weighting)),
            max_position=float(str(data.get("max_position", cls.max_position))),
            max_exposure=float(str(data.get("max_exposure", cls.max_exposure))),
            max_group_exposure=float(str(data.get("max_group_exposure", cls.max_group_exposure))),
            max_turnover=(float(str(data["max_turnover"])) if data.get("max_turnover") is not None else None),
            min_position=float(str(data.get("min_position", cls.min_position))),
            volatility_floor=float(str(data.get("volatility_floor", cls.volatility_floor))),
        )

    def validate(self) -> None:
        if self.top_n <= 0:
            raise ValueError("top_n must be positive")
        if not 0 < self.max_position <= 1:
            raise ValueError("max_position must be in (0, 1]")
        if not 0 < self.max_exposure <= 1:
            raise ValueError("max_exposure must be in (0, 1]")
        if not 0 < self.max_group_exposure <= 1:
            raise ValueError("max_group_exposure must be in (0, 1]")
        if self.max_turnover is not None and self.max_turnover < 0:
            raise ValueError("max_turnover must be non-negative")
        if not 0 <= self.min_position <= self.max_position:
            raise ValueError("min_position must be in [0, max_position]")
        if self.volatility_floor <= 0:
            raise ValueError("volatility_floor must be positive")
        if self.weighting not in {"equal", "rank", "score", "score_vol"}:
            raise ValueError(f"unsupported weighting method: {self.weighting}")


def _normalise_selection(selection: pd.DataFrame) -> pd.DataFrame:
    if "instrument" not in selection.columns:
        if selection.index.name == "instrument":
            selection = selection.reset_index()
        else:
            raise ValueError("selection must contain an instrument column")
    if "score" not in selection.columns:
        raise ValueError("selection must contain a score column")
    frame = selection.copy()
    frame["instrument"] = frame["instrument"].astype(str).str.upper().str.strip()
    frame["score"] = pd.to_numeric(frame["score"], errors="coerce")
    frame = frame.dropna(subset=["instrument", "score"])
    frame = frame.drop_duplicates("instrument", keep="first")
    return frame.sort_values(["score", "instrument"], ascending=[False, True]).reset_index(drop=True)


def _raw_weight_signal(frame: pd.DataFrame, policy: PortfolioPolicy) -> pd.Series:
    if policy.weighting == "equal":
        raw = pd.Series(1.0, index=frame.index)
    elif policy.weighting == "rank":
        raw = pd.Series(np.arange(len(frame), 0, -1, dtype=float), index=frame.index)
    else:
        scores = frame["score"].astype(float)
        raw = scores - scores.min() + max(float(scores.std(ddof=0)) * 0.01, 1e-8)
        if policy.weighting == "score_vol":
            if "volatility" not in frame.columns:
                raise ValueError("score_vol weighting requires a volatility column")
            vol = pd.to_numeric(frame["volatility"], errors="coerce").clip(lower=policy.volatility_floor)
            vol = vol.fillna(vol.median() if vol.notna().any() else policy.volatility_floor)
            raw = raw / vol
    raw = raw.replace([np.inf, -np.inf], np.nan).fillna(0.0).clip(lower=0.0)
    if float(raw.sum()) <= 0:
        raw[:] = 1.0
    return raw


def _bounded_allocate(
    raw: pd.Series,
    groups: pd.Series,
    *,
    exposure: float,
    position_cap: float,
    group_cap: float,
) -> pd.Series:
    """Allocate a long-only portfolio under position/group caps.

    The water-filling loop scales each group's proposed increment to its remaining
    group capacity, then redistributes residual cash to eligible names. If the caps
    make the requested exposure infeasible, the residual remains cash.
    """

    weights = pd.Series(0.0, index=raw.index, dtype=float)
    desired = min(exposure, len(raw) * position_cap, groups.nunique() * group_cap)
    for _ in range(max(20, len(raw) * 8)):
        remaining = desired - float(weights.sum())
        if remaining <= 1e-12:
            break
        group_used = weights.groupby(groups).sum()
        eligible = [
            idx
            for idx in raw.index
            if position_cap - float(weights.loc[idx]) > 1e-12
            and group_cap - float(group_used.get(groups.loc[idx], 0.0)) > 1e-12
        ]
        if not eligible:
            break
        signal = raw.loc[eligible].clip(lower=0.0)
        if float(signal.sum()) <= 0:
            signal[:] = 1.0
        proposed = remaining * signal / float(signal.sum())
        additions = pd.Series(0.0, index=eligible, dtype=float)
        for idx in eligible:
            additions.loc[idx] = min(float(proposed.loc[idx]), position_cap - float(weights.loc[idx]))
        for group, idxs in groups.loc[eligible].groupby(groups.loc[eligible]).groups.items():
            idxs = list(idxs)
            room = max(0.0, group_cap - float(group_used.get(group, 0.0)))
            proposed_group = float(additions.loc[idxs].sum())
            if proposed_group > room > 0:
                additions.loc[idxs] *= room / proposed_group
            elif room <= 0:
                additions.loc[idxs] = 0.0
        allocated = float(additions.sum())
        if allocated <= 1e-12:
            break
        weights.loc[additions.index] += additions
    return weights.clip(lower=0.0)


def _current_weight_series(current: pd.DataFrame | pd.Series | None) -> pd.Series:
    if current is None:
        return pd.Series(dtype=float)
    if isinstance(current, pd.Series):
        result = current.copy()
    else:
        if "instrument" not in current.columns:
            raise ValueError("current portfolio must contain instrument")
        weight_col = "target_weight" if "target_weight" in current.columns else "current_weight"
        if weight_col not in current.columns:
            raise ValueError("current portfolio must contain target_weight or current_weight")
        result = current.set_index("instrument")[weight_col]
    result.index = result.index.astype(str).str.upper().str.strip()
    return pd.to_numeric(result, errors="coerce").fillna(0.0).groupby(level=0).sum().clip(lower=0.0)


def portfolio_turnover(target: pd.Series, current: pd.Series) -> float:
    universe = target.index.union(current.index)
    return float(
        0.5
        * (target.reindex(universe, fill_value=0.0) - current.reindex(universe, fill_value=0.0)).abs().sum()
    )


def construct_target_portfolio(
    selection: pd.DataFrame,
    policy: PortfolioPolicy,
    *,
    current: pd.DataFrame | pd.Series | None = None,
) -> pd.DataFrame:
    policy.validate()
    frame = _normalise_selection(selection)
    if policy.min_score is not None:
        frame = frame.loc[frame["score"] >= policy.min_score]
    frame = frame.head(policy.top_n).copy()
    output_columns = ["instrument", "score", "target_weight", "weighting", "group"]
    if frame.empty:
        return pd.DataFrame(columns=output_columns)

    raw = _raw_weight_signal(frame, policy)
    groups = (
        frame["group"].fillna("UNKNOWN").astype(str)
        if "group" in frame.columns
        else frame["instrument"].astype(str)
    )
    weights = _bounded_allocate(
        raw,
        groups,
        exposure=policy.max_exposure,
        position_cap=policy.max_position,
        group_cap=policy.max_group_exposure,
    )
    # Critical invariant: attach weights while both objects still have the same row index.
    # Re-indexing a RangeIndex weight series with instrument codes was the original P0 bug.
    frame["target_weight"] = weights.to_numpy(dtype=float)
    frame["group"] = groups.to_numpy(dtype=str)

    current_weights = _current_weight_series(current)
    target = frame.set_index("instrument")["target_weight"]
    pre_turnover = portfolio_turnover(target, current_weights)
    if policy.max_turnover is not None and pre_turnover > policy.max_turnover > 0:
        universe = target.index.union(current_weights.index)
        old = current_weights.reindex(universe, fill_value=0.0)
        desired = target.reindex(universe, fill_value=0.0)
        alpha = policy.max_turnover / pre_turnover
        blended = (old + alpha * (desired - old)).clip(lower=0.0, upper=policy.max_position)
        frame = frame.set_index("instrument")
        carry = blended.index.difference(frame.index)
        if len(carry):
            carry_frame = pd.DataFrame(index=carry)
            carry_frame["score"] = np.nan
            carry_frame["group"] = "CARRYOVER"
            frame = pd.concat([frame, carry_frame], axis=0, sort=False)
        frame["target_weight"] = blended.reindex(frame.index, fill_value=0.0)
        frame = frame.reset_index()

    frame.loc[frame["target_weight"] < policy.min_position, "target_weight"] = 0.0
    frame["weighting"] = policy.weighting
    frame["pre_trade_turnover"] = pre_turnover
    optional = [
        c
        for c in ("volatility", "group", "adv20_amount", "signal_date", "model_id", "dataset_id")
        if c in frame
    ]
    return frame[
        [
            "instrument",
            "score",
            "target_weight",
            "weighting",
            *[c for c in optional if c != "group"],
            "group",
            "pre_trade_turnover",
        ]
    ]
