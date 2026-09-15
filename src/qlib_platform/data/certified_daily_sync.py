from __future__ import annotations

from typing import Any, Mapping

import pandas as pd

from qlib_platform.data.production_daily_sync import ProductionDailySyncService
from qlib_platform.data.universe import configured_universe


def _daily_run_freshness_config(settings_data: Mapping[str, Any]) -> Mapping[str, Any]:
    production = settings_data.get("production", {})
    production = production if isinstance(production, Mapping) else {}
    daily_run = production.get("daily_run", {})
    daily_run = daily_run if isinstance(daily_run, Mapping) else {}
    freshness = daily_run.get("freshness", {})
    return freshness if isinstance(freshness, Mapping) else {}


class CertifiedDailySyncService(ProductionDailySyncService):
    """Production sync with the complete #129 release freshness contract."""

    def _market_cross_section_gate(self, target_session: str) -> dict[str, Any]:
        cfg = _daily_run_freshness_config(self.settings.data)
        minimum_ratio = float(cfg.get("min_market_coverage_ratio", 0.95))
        if not 0 < minimum_ratio <= 1:
            raise ValueError("production.daily_run.freshness.min_market_coverage_ratio must be in (0, 1]")

        dates = [date for date in self.store.list_dates("daily") if date <= target_session]
        previous = next((date for date in reversed(dates) if date < target_session), None)
        if previous is None:
            return {
                "fresh": True,
                "target_session": target_session,
                "previous_session": None,
                "coverage_ratio": None,
                "minimum_ratio": minimum_ratio,
                "reason": "no_previous_session_for_ratio_gate",
            }

        current = self.store.read("daily", target_session)
        prior = self.store.read("daily", previous)
        current_count = int(current["ts_code"].astype(str).nunique()) if "ts_code" in current else 0
        previous_count = int(prior["ts_code"].astype(str).nunique()) if "ts_code" in prior else 0
        ratio = current_count / max(1, previous_count)
        return {
            "fresh": current_count > 0 and ratio >= minimum_ratio,
            "target_session": target_session,
            "previous_session": previous,
            "target_symbols": current_count,
            "previous_symbols": previous_count,
            "coverage_ratio": ratio,
            "minimum_ratio": minimum_ratio,
        }

    def _freshness_gate(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        result = super()._freshness_gate(plan)
        failures = [str(value) for value in result.get("failures", [])]
        target_session = str(plan["target_session"])
        target = pd.Timestamp(target_session).normalize()

        market_cross_section = self._market_cross_section_gate(target_session)
        result["market_cross_section"] = market_cross_section
        if not market_cross_section["fresh"]:
            failures.append(f"market_cross_section:{target_session}")

        configured = configured_universe(self.settings)
        if configured is None:
            result["universe"] = {
                "fresh": True,
                "kind": "all",
                "active_members": None,
            }
        else:
            cfg = _daily_run_freshness_config(self.settings.data)
            max_snapshot_age = int(cfg.get("max_universe_snapshot_age_calendar_days", 45))
            if max_snapshot_age < 1:
                raise ValueError(
                    "production.daily_run.freshness.max_universe_snapshot_age_calendar_days must be positive"
                )
            name, index_code, path = configured
            active_members = 0
            snapshot_date: str | None = None
            snapshot_age_days: int | None = None
            error: str | None = None
            if not path.is_file():
                error = "membership file missing"
            else:
                try:
                    frame = pd.read_parquet(path)
                    required = {"instrument", "snapshot_date", "effective_from", "effective_to"}
                    missing = sorted(required - set(frame.columns))
                    if missing:
                        error = f"missing columns: {missing}"
                    else:
                        effective_from = pd.to_datetime(frame["effective_from"], errors="coerce").dt.normalize()
                        effective_to = pd.to_datetime(frame["effective_to"], errors="coerce").dt.normalize()
                        active = effective_from.le(target) & effective_to.ge(target)
                        active_members = int(frame.loc[active, "instrument"].nunique())
                        snapshots = pd.to_datetime(
                            frame.loc[active, "snapshot_date"], errors="coerce"
                        ).dropna()
                        if active_members <= 0:
                            error = "no point-in-time members cover target session"
                        elif snapshots.empty:
                            error = "active membership has no snapshot_date"
                        else:
                            latest_snapshot = pd.Timestamp(snapshots.max()).normalize()
                            snapshot_date = latest_snapshot.strftime("%Y%m%d")
                            snapshot_age_days = int((target - latest_snapshot).days)
                            if snapshot_age_days < 0:
                                error = "active membership snapshot is from the future"
                            elif snapshot_age_days > max_snapshot_age:
                                error = (
                                    f"active membership snapshot age {snapshot_age_days}d exceeds "
                                    f"{max_snapshot_age}d"
                                )
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
            fresh = error is None
            result["universe"] = {
                "fresh": fresh,
                "kind": name,
                "index_code": index_code,
                "path": str(path),
                "active_members": active_members,
                "snapshot_date": snapshot_date,
                "snapshot_age_calendar_days": snapshot_age_days,
                "max_snapshot_age_calendar_days": max_snapshot_age,
                "error": error,
            }
            if not fresh:
                failures.append(f"universe:{name}:{target_session}")

        result["failures"] = failures
        result["passed"] = not failures
        return result
