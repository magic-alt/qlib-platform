from __future__ import annotations

from dataclasses import asdict, dataclass

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
    def from_mapping(cls, value: dict[str, object] | None) -> "HardRiskPolicy":
        data = value or {}
        return cls(**{key: data[key] for key in cls.__dataclass_fields__ if key in data})


def pretrade_risk_check(targets: pd.DataFrame, policy: HardRiskPolicy, *, daily_pnl_pct: float) -> dict[str, object]:
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
        sector_max = float(active.assign(_weight=weights.loc[active.index]).groupby("sector")["_weight"].sum().max())
    checks = {
        "gross_exposure": gross <= policy.max_gross_exposure,
        "single_name": single <= policy.max_single_name,
        "sector_exposure": sector_max <= policy.max_sector_exposure,
        "daily_loss": float(daily_pnl_pct) >= -policy.max_daily_loss,
    }
    result = {"approved": all(checks.values()), "checks": checks, "policy": asdict(policy), "gross": gross,
              "max_single": single, "max_sector": sector_max, "daily_pnl_pct": float(daily_pnl_pct)}
    if not result["approved"]:
        raise RiskLimitError(f"hard risk rejected order release: {checks}")
    return result
