from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping

import numpy as np
import pandas as pd


class RiskLimitError(ValueError):
    pass


@dataclass(frozen=True)
class HardRiskPolicy:
    max_gross_exposure: float = 0.95
    max_single_name: float = 0.10
    max_sector_exposure: float = 0.30
    max_daily_loss: float = 0.03
    kill_switch: bool = False

    @classmethod
    def from_mapping(cls, value: Mapping[str, object] | None) -> "HardRiskPolicy":
        data = value or {}
        kill_switch = data.get("kill_switch", cls.kill_switch)
        if isinstance(kill_switch, str):
            kill_switch = kill_switch.strip().lower() in {"1", "true", "yes", "y"}
        policy = cls(
            max_gross_exposure=float(str(data.get("max_gross_exposure", cls.max_gross_exposure))),
            max_single_name=float(str(data.get("max_single_name", cls.max_single_name))),
            max_sector_exposure=float(str(data.get("max_sector_exposure", cls.max_sector_exposure))),
            max_daily_loss=float(str(data.get("max_daily_loss", cls.max_daily_loss))),
            kill_switch=bool(kill_switch),
        )
        if not 0 < policy.max_gross_exposure <= 1:
            raise RiskLimitError("max_gross_exposure must be in (0, 1]")
        if not 0 < policy.max_single_name <= 1:
            raise RiskLimitError("max_single_name must be in (0, 1]")
        if not 0 < policy.max_sector_exposure <= 1:
            raise RiskLimitError("max_sector_exposure must be in (0, 1]")
        if not 0 < policy.max_daily_loss < 1:
            raise RiskLimitError("max_daily_loss must be in (0, 1)")
        return policy


@dataclass(frozen=True)
class ExposureOverlayPolicy:
    """Continuous exposure control applied after alpha ranking and sizing."""

    enabled: bool = False
    target_annual_volatility: float = 0.15
    soft_drawdown: float = 0.10
    hard_drawdown: float = 0.20
    minimum_signal_dispersion: float = 0.05
    minimum_exposure_scale: float = 0.0
    maximum_exposure_scale: float = 1.0

    @classmethod
    def from_mapping(cls, value: Mapping[str, object] | None) -> "ExposureOverlayPolicy":
        data = value or {}
        policy = cls(
            enabled=bool(data.get("enabled", cls.enabled)),
            target_annual_volatility=float(
                str(data.get("target_annual_volatility", cls.target_annual_volatility))
            ),
            soft_drawdown=float(str(data.get("soft_drawdown", cls.soft_drawdown))),
            hard_drawdown=float(str(data.get("hard_drawdown", cls.hard_drawdown))),
            minimum_signal_dispersion=float(
                str(data.get("minimum_signal_dispersion", cls.minimum_signal_dispersion))
            ),
            minimum_exposure_scale=float(str(data.get("minimum_exposure_scale", cls.minimum_exposure_scale))),
            maximum_exposure_scale=float(str(data.get("maximum_exposure_scale", cls.maximum_exposure_scale))),
        )
        if policy.target_annual_volatility <= 0:
            raise RiskLimitError("target_annual_volatility must be positive")
        if not 0 <= policy.soft_drawdown < policy.hard_drawdown < 1:
            raise RiskLimitError("drawdown thresholds must satisfy 0 <= soft < hard < 1")
        if policy.minimum_signal_dispersion <= 0:
            raise RiskLimitError("minimum_signal_dispersion must be positive")
        if not 0 <= policy.minimum_exposure_scale <= policy.maximum_exposure_scale <= 1:
            raise RiskLimitError("exposure scales must satisfy 0 <= minimum <= maximum <= 1")
        return policy


def exposure_scale(
    policy: ExposureOverlayPolicy,
    *,
    realized_annual_volatility: float,
    current_drawdown: float,
    signal_dispersion: float,
) -> dict[str, float]:
    """Combine volatility, drawdown and signal-confidence throttles conservatively."""

    if not policy.enabled:
        return {"scale": 1.0, "volatilityScale": 1.0, "drawdownScale": 1.0, "signalScale": 1.0}
    values = [realized_annual_volatility, current_drawdown, signal_dispersion]
    if not all(np.isfinite(value) for value in values):
        raise RiskLimitError("exposure overlay inputs must be finite")
    if realized_annual_volatility <= 0 or current_drawdown > 0 or signal_dispersion < 0:
        raise RiskLimitError("invalid exposure overlay state")
    volatility_scale = min(1.0, policy.target_annual_volatility / realized_annual_volatility)
    if current_drawdown <= -policy.hard_drawdown:
        drawdown_scale = 0.0
    elif current_drawdown >= -policy.soft_drawdown:
        drawdown_scale = 1.0
    else:
        drawdown_scale = (policy.hard_drawdown + current_drawdown) / (
            policy.hard_drawdown - policy.soft_drawdown
        )
    signal_scale = min(1.0, signal_dispersion / policy.minimum_signal_dispersion)
    raw = min(volatility_scale, drawdown_scale, signal_scale)
    scale = min(policy.maximum_exposure_scale, max(policy.minimum_exposure_scale, raw))
    return {
        "scale": float(scale),
        "volatilityScale": float(volatility_scale),
        "drawdownScale": float(drawdown_scale),
        "signalScale": float(signal_scale),
    }


def apply_exposure_overlay(
    targets: pd.DataFrame,
    policy: ExposureOverlayPolicy,
    *,
    realized_annual_volatility: float,
    current_drawdown: float,
    signal_dispersion: float,
) -> tuple[pd.DataFrame, dict[str, float]]:
    if "target_weight" not in targets:
        raise RiskLimitError("targets missing target_weight")
    state = exposure_scale(
        policy,
        realized_annual_volatility=realized_annual_volatility,
        current_drawdown=current_drawdown,
        signal_dispersion=signal_dispersion,
    )
    result = targets.copy()
    weights = pd.to_numeric(result["target_weight"], errors="coerce")
    if weights.isna().any() or (weights < 0).any():
        raise RiskLimitError("target weights must be finite non-negative values")
    result["pre_overlay_target_weight"] = weights
    result["target_weight"] = weights * state["scale"]
    result["exposure_scale"] = state["scale"]
    return result, state


def pretrade_risk_check(
    targets: pd.DataFrame, policy: HardRiskPolicy, *, daily_pnl_pct: float
) -> dict[str, object]:
    """Validate the target portfolio before an order is handed to a broker.

    This deliberately fails closed: a missing target weight, sector (where
    multiple names are present), or daily P&L prevents a release.
    """
    if policy.kill_switch:
        raise RiskLimitError("kill switch is enabled")
    if "target_weight" not in targets:
        raise RiskLimitError("targets missing target_weight")
    weights = pd.to_numeric(targets["target_weight"], errors="coerce")
    if weights.isna().any() or (weights < 0).any():
        raise RiskLimitError("target weights must be finite non-negative values")
    if pd.isna(daily_pnl_pct):
        raise RiskLimitError("daily_pnl_pct is required for hard risk approval")
    gross = float(weights.sum())
    single = float(weights.max()) if len(weights) else 0.0
    sector_max = 0.0
    active = targets.loc[weights > 0].copy()
    if len(active) > 1:
        if "sector" not in active or active["sector"].isna().any():
            raise RiskLimitError("sector is required for multi-name hard risk approval")
        sector_max = float(
            active.assign(_weight=weights.loc[active.index]).groupby("sector")["_weight"].sum().max()
        )
    checks = {
        "gross_exposure": gross <= policy.max_gross_exposure,
        "single_name": single <= policy.max_single_name,
        "sector_exposure": sector_max <= policy.max_sector_exposure,
        "daily_loss": float(daily_pnl_pct) >= -policy.max_daily_loss,
    }
    result = {
        "approved": all(checks.values()),
        "checks": checks,
        "policy": asdict(policy),
        "gross": gross,
        "max_single": single,
        "max_sector": sector_max,
        "daily_pnl_pct": float(daily_pnl_pct),
    }
    if not result["approved"]:
        raise RiskLimitError(f"hard risk rejected order release: {checks}")
    return result
