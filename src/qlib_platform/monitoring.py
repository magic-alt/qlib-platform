from __future__ import annotations

import math
from typing import Iterable

import numpy as np
import pandas as pd


def population_stability_index(
    reference: Iterable[float], current: Iterable[float], *, bins: int = 10
) -> float:
    ref = pd.Series(reference, dtype=float).replace([np.inf, -np.inf], np.nan).dropna()
    cur = pd.Series(current, dtype=float).replace([np.inf, -np.inf], np.nan).dropna()
    if ref.empty or cur.empty:
        return float("nan")
    edges = np.unique(np.quantile(ref, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        return 0.0
    edges[0], edges[-1] = -np.inf, np.inf
    ref_pct = pd.cut(ref, edges, include_lowest=True).value_counts(normalize=True, sort=False)
    cur_pct = pd.cut(cur, edges, include_lowest=True).value_counts(normalize=True, sort=False)
    epsilon = 1e-6
    return float(
        (
            (cur_pct.clip(lower=epsilon) - ref_pct.clip(lower=epsilon))
            * np.log(cur_pct.clip(lower=epsilon) / ref_pct.clip(lower=epsilon))
        ).sum()
    )


def signal_drift_snapshot(
    reference: pd.Series,
    current: pd.Series,
    *,
    topk: int,
) -> dict[str, float | int]:
    if topk <= 0:
        raise ValueError("topk must be positive")
    reference = pd.to_numeric(reference, errors="coerce").dropna()
    current = pd.to_numeric(current, errors="coerce").dropna()
    if reference.index.has_duplicates or current.index.has_duplicates:
        raise ValueError("drift inputs require unique instrument indexes")
    shared = reference.index.intersection(current.index)
    ref_top = set(reference.sort_values(ascending=False).head(topk).index.astype(str))
    cur_top = set(current.sort_values(ascending=False).head(topk).index.astype(str))
    denominator = max(1, min(topk, len(ref_top), len(cur_top)))
    topk_overlap = len(ref_top & cur_top) / denominator
    if len(shared) >= 2:
        reference_rank = reference.loc[shared].rank(method="average", pct=True, ascending=False)
        current_rank = current.loc[shared].rank(method="average", pct=True, ascending=False)
        rank_turnover = float((reference_rank - current_rank).abs().mean())
    else:
        rank_turnover = float("nan")
    return {
        "scorePsi": population_stability_index(reference.values, current.values),
        "topkOverlap": float(topk_overlap),
        "rankTurnover": rank_turnover,
        "sharedInstrumentCount": int(len(shared)),
    }


def evaluate_signal_drift(
    reference: pd.Series,
    current: pd.Series,
    *,
    topk: int,
    max_score_psi: float,
    min_topk_overlap: float,
    max_rank_turnover: float,
) -> tuple[dict[str, float | int], list[str]]:
    metrics = signal_drift_snapshot(reference, current, topk=topk)
    reasons: list[str] = []
    score_psi = float(metrics["scorePsi"])
    rank_turnover = float(metrics["rankTurnover"])
    if not np.isfinite(score_psi) or score_psi > max_score_psi:
        reasons.append("SCORE_PSI_HIGH")
    if float(metrics["topkOverlap"]) < min_topk_overlap:
        reasons.append("TOPK_OVERLAP_LOW")
    if not np.isfinite(rank_turnover) or rank_turnover > max_rank_turnover:
        reasons.append("RANK_TURNOVER_HIGH")
    return metrics, reasons


def portfolio_risk_snapshot(
    targets: pd.DataFrame,
    *,
    group_col: str = "group",
    adv_col: str = "adv20_amount",
    portfolio_value: float | None = None,
    max_participation_rate: float = 0.05,
) -> dict[str, object]:
    if not {"instrument", "target_weight"}.issubset(targets.columns):
        raise ValueError("targets require instrument and target_weight")
    weights = pd.to_numeric(targets["target_weight"], errors="coerce").fillna(0.0)
    hhi = float((weights**2).sum())
    effective_names = 1.0 / hhi if hhi > 0 else 0.0
    group_exposure = (
        targets.assign(_weight=weights)
        .groupby(group_col)["_weight"]
        .sum()
        .sort_values(ascending=False)
        .to_dict()
        if group_col in targets.columns
        else {}
    )
    capacity = None
    if portfolio_value is not None and adv_col in targets.columns:
        adv = pd.to_numeric(targets[adv_col], errors="coerce")
        required = weights * float(portfolio_value)
        days = required / (adv * max_participation_rate)
        finite = days.replace([np.inf, -np.inf], np.nan).dropna()
        capacity = {
            "max_days_to_trade": float(finite.max()) if not finite.empty else math.inf,
            "median_days_to_trade": float(finite.median()) if not finite.empty else math.inf,
        }
    return {
        "gross_exposure": float(weights.abs().sum()),
        "net_exposure": float(weights.sum()),
        "max_position": float(weights.max()) if len(weights) else 0.0,
        "hhi": hhi,
        "effective_names": effective_names,
        "group_exposure": group_exposure,
        "capacity": capacity,
    }
