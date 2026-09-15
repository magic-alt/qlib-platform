from __future__ import annotations

import json

from qlib_platform.data.production_daily_sync import ProductionDailySyncService
from qlib_platform.runtime import daily_research_run as base
from qlib_platform.settings import Settings


class DailyResearchRun(base.DailyResearchRun):
    """Daily DAG bound to the metadata-only planner and incremental factor cache."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.sync = ProductionDailySyncService(settings)
        self.root = settings.paths.state / "daily_run"


def main() -> int:
    args = base.parser().parse_args()
    if args.resume and args.plan:
        raise ValueError("--resume cannot be combined with --plan")
    if args.backfill and (args.resume or args.as_of):
        raise ValueError("--backfill cannot be combined with --resume/--as-of")
    settings = Settings.load(args.config, require_tushare=False, create_dirs=True)
    runner = DailyResearchRun(settings)

    if args.backfill:
        outputs = runner.backfill(
            args.backfill[0],
            args.backfill[1],
            mode=args.mode,
            force_full=args.force_full,
        )
        print(json.dumps({"backfill": [str(path) for path in outputs]}, ensure_ascii=False))
        return 0

    if args.resume:
        path = runner.execute_plan(
            args.resume,
            force_full=args.force_full,
            regression=args.regression,
        )
        print(json.dumps(base._read_json(path), ensure_ascii=False))
        return 0

    plan_path = runner.plan(as_of=args.as_of, mode=args.mode)
    if args.plan:
        print(json.dumps(base._read_json(plan_path), ensure_ascii=False))
        return 0
    plan = base._read_json(plan_path)
    result = runner.execute_plan(
        str(plan["plan_id"]),
        force_full=args.force_full,
        regression=args.regression,
    )
    print(json.dumps(base._read_json(result), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
