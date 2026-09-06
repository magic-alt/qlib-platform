from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class LotSizedPortfolio:
    shares: pd.Series
    weights: pd.Series
    invested_value: float
    residual_cash: float


def _lot_vector(lot_sizes: int | pd.Series, instruments: pd.Index) -> np.ndarray:
    if isinstance(lot_sizes, int):
        if lot_sizes <= 0:
            raise ValueError("lot_size must be positive")
        return np.full(len(instruments), lot_sizes, dtype=np.int64)
    if not lot_sizes.index.equals(instruments):
        raise ValueError("lot_sizes must exactly match target-weight index")
    numeric: np.ndarray = np.asarray(pd.to_numeric(lot_sizes, errors="coerce"), dtype=float)
    if not np.isfinite(numeric).all() or bool(np.any(numeric <= 0)):
        raise ValueError("lot_sizes must contain finite positive values")
    rounded: np.ndarray = np.rint(numeric).astype(np.int64)
    if not np.allclose(numeric, rounded):
        raise ValueError("lot_sizes must contain integer share counts")
    return rounded


def round_weights_to_lots(
    weights: pd.Series,
    prices: pd.Series,
    *,
    portfolio_value: float,
    lot_sizes: int | pd.Series = 100,
) -> LotSizedPortfolio:
    """Translate continuous target weights into executable round lots.

    The optimizer remains weight-native. This implementation layer converts
    those weights into shares using the supplied research price snapshot and
    NAV. It first floors every target to a valid lot and then spends residual
    cash only when adding one lot reduces absolute target-notional error.
    """

    if portfolio_value <= 0 or not np.isfinite(portfolio_value):
        raise ValueError("portfolio_value must be finite and positive")
    if not prices.index.equals(weights.index):
        raise ValueError("prices must exactly match target-weight index")
    weight_values = pd.to_numeric(weights, errors="coerce").to_numpy(dtype=float)
    price_values = pd.to_numeric(prices, errors="coerce").to_numpy(dtype=float)
    if not np.isfinite(weight_values).all() or bool(np.any(weight_values < -1e-12)):
        raise ValueError("target weights must be finite and non-negative")
    if not np.isfinite(price_values).all() or bool(np.any(price_values <= 0)):
        raise ValueError("prices must be finite and strictly positive")
    if float(weight_values.sum()) > 1.0 + 1e-8:
        raise ValueError("target weights cannot invest more than portfolio_value")

    lots = _lot_vector(lot_sizes, weights.index)
    lot_cost = price_values * lots
    target_notional = portfolio_value * weight_values
    lot_counts = np.floor(target_notional / lot_cost + 1e-12).astype(np.int64)
    invested = lot_counts.astype(float) * lot_cost
    remaining = portfolio_value - float(invested.sum())

    while True:
        affordable = lot_cost <= remaining + 1e-10
        if not bool(np.any(affordable)):
            break
        current_error = np.abs(target_notional - invested)
        next_error = np.abs(target_notional - (invested + lot_cost))
        improvement = current_error - next_error
        improvement[~affordable] = -np.inf
        position = int(np.argmax(improvement))
        if not np.isfinite(improvement[position]) or improvement[position] <= 1e-12:
            break
        lot_counts[position] += 1
        invested[position] += lot_cost[position]
        remaining -= lot_cost[position]

    shares = lot_counts * lots
    implemented_notional = shares.astype(float) * price_values
    invested_value = float(implemented_notional.sum())
    residual_cash = float(portfolio_value - invested_value)
    implemented_weights = implemented_notional / portfolio_value
    return LotSizedPortfolio(
        shares=pd.Series(shares, index=weights.index, name="target_shares", dtype="int64"),
        weights=pd.Series(
            implemented_weights,
            index=weights.index,
            name="implemented_weight",
            dtype=float,
        ),
        invested_value=invested_value,
        residual_cash=residual_cash,
    )
