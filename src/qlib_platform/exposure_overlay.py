from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
import pandas as pd


class ExposureOverlayError(ValueError):
    """Raised when research exposure-overlay inputs violate the policy contract."""


@dataclass(frozen=True)
class ExposureOverlayPolicy:
    """Continuous research exposure control applied after alpha ranking and sizing."""

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
            raise ExposureOverlayError("target_annual_volatility must be positive")
        if not 0 <= policy.soft_drawdown < policy.hard_drawdown < 1:
            raise ExposureOverlayError("drawdown thresholds must satisfy 0 <= soft < hard < 1")
        if policy.minimum_signal_dispersion <= 0:
            raise ExposureOverlayError("minimum_signal_dispersion must be positive")
        if not 0 <= policy.minimum_exposure_scale <= policy.maximum_exposure_scale <= 1:
            raise ExposureOverlayError("exposure scales must satisfy 0 <= minimum <= maximum <= 1")
        return policy


def exposure_scale(
    policy: ExposureOverlayPolicy,
    *,
    realized_annual_volatility: float,
    current_drawdown: float,
    signal_dispersion: float,
) -> dict[str, float]:
    """Combine research volatility, drawdown and signal-confidence throttles."""

    if not policy.enabled:
        return {"scale": 1.0, "volatilityScale": 1.0, "drawdownScale": 1.0, "signalScale": 1.0}
    values = [realized_annual_volatility, current_drawdown, signal_dispersion]
    if not all(np.isfinite(value) for value in values):
        raise ExposureOverlayError("exposure overlay inputs must be finite")
    if realized_annual_volatility <= 0 or current_drawdown > 0 or signal_dispersion < 0:
        raise ExposureOverlayError("invalid exposure overlay state")
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
        raise ExposureOverlayError("targets missing target_weight")
    state = exposure_scale(
        policy,
        realized_annual_volatility=realized_annual_volatility,
        current_drawdown=current_drawdown,
        signal_dispersion=signal_dispersion,
    )
    result = targets.copy()
    weights = pd.to_numeric(result["target_weight"], errors="coerce")
    if weights.isna().any() or (weights < 0).any():
        raise ExposureOverlayError("target weights must be finite non-negative values")
    result["pre_overlay_target_weight"] = weights
    result["target_weight"] = weights * state["scale"]
    result["exposure_scale"] = state["scale"]
    return result, state
