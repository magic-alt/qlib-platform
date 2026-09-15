from __future__ import annotations

from typing import Any, Mapping

import pandas as pd

from qlib_platform.data.production_daily_sync import ProductionDailySyncService
from qlib_platform.data.universe import configured_universe


class CertifiedDailySyncService(ProductionDailySyncService):
    """Production sync with the complete #129 release freshness contract."""

    def _freshness_gate(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        result = super()._freshness_gate(plan)
        failures = [str(value) for value in result.get("failures", [])]
        configured = configured_universe(self.settings)
        target = pd.Timestamp(str(plan["target_session"])).normalize()

        if configured is None:
            result["universe"] = {
                "fresh": True,
                "kind": "all",
                "active_members": None,
            }
        else:
            name, index_code, path = configured
            active_members = 0
            error: str | None = None
            if not path.is_file():
                error = "membership file missing"
            else:
                try:
                    frame = pd.read_parquet(path)
                    required = {"instrument", "effective_from", "effective_to"}
                    missing = sorted(required - set(frame.columns))
                    if missing:
                        error = f"missing columns: {missing}"
                    else:
                        effective_from = pd.to_datetime(frame["effective_from"], errors="coerce").dt.normalize()
                        effective_to = pd.to_datetime(frame["effective_to"], errors="coerce").dt.normalize()
                        active = effective_from.le(target) & effective_to.ge(target)
                        active_members = int(frame.loc[active, "instrument"].nunique())
                        if active_members <= 0:
                            error = "no point-in-time members cover target session"
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
            fresh = error is None
            result["universe"] = {
                "fresh": fresh,
                "kind": name,
                "index_code": index_code,
                "path": str(path),
                "active_members": active_members,
                "error": error,
            }
            if not fresh:
                failures.append(f"universe:{name}:{plan['target_session']}")

        result["failures"] = failures
        result["passed"] = not failures
        return result
