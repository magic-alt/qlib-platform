from __future__ import annotations

import sys
from pathlib import Path

from qlib_platform.research.evidence.run_adapters import record_daily_run
from qlib_platform.runtime import production_daily_run


_ProductionDailyRun = production_daily_run.DailyResearchRun


class ManifestDailyResearchRun(_ProductionDailyRun):
    def execute_plan(
        self,
        plan_id: str,
        *,
        force_full: bool = False,
        regression: bool | None = None,
        backfill: bool = False,
    ) -> Path:
        try:
            path = super().execute_plan(
                plan_id,
                force_full=force_full,
                regression=regression,
                backfill=backfill,
            )
        except Exception:
            failed = self._manifest_path(plan_id)
            if failed.is_file():
                record_daily_run(self.settings, failed, argv=sys.argv[1:])
            raise
        record_daily_run(self.settings, path, argv=sys.argv[1:])
        return path


def main() -> int:
    original = production_daily_run.DailyResearchRun
    setattr(production_daily_run, "DailyResearchRun", ManifestDailyResearchRun)
    try:
        return production_daily_run.main()
    finally:
        setattr(production_daily_run, "DailyResearchRun", original)


if __name__ == "__main__":
    raise SystemExit(main())
