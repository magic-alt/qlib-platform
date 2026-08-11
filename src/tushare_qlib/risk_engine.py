from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping

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
