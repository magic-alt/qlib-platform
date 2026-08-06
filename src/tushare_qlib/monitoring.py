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
    return float(((cur_pct.clip(lower=epsilon) - ref_pct.clip(lower=epsilon)) * np.log(cur_pct.clip(lower=epsilon) / ref_pct.clip(lower=epsilon))).sum())


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
        targets.assign(_weight=weights).groupby(group_col)["_weight"].sum().sort_values(ascending=False).to_dict()
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
