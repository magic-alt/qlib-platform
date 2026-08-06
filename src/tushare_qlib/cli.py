from __future__ import annotations

import argparse

from .extract import Extractor
from .normalize import build_all_curated, build_curated_day, export_full_staging, export_incremental_staging
from .qlib_export import dump_full, dump_update
from .settings import Settings
from .train_select import train_backtest_select


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Tushare Pro -> Qlib A-share pipeline")
    p.add_argument("--config", default="configs/pipeline.yaml")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init-metadata")
    b = sub.add_parser("backfill")
    b.add_argument("--start")
    b.add_argument("--end")
    b.add_argument("--force", action="store_true")

    c = sub.add_parser("curate")
    c.add_argument("--start")
    c.add_argument("--end")

    d = sub.add_parser("curate-day")
    d.add_argument("trade_date")
    d.add_argument("--force", action="store_true")

    sf = sub.add_parser("stage-full")
    sf.add_argument("--force", action="store_true")
    df = sub.add_parser("dump-full")
    df.add_argument("--single-thread", action="store_true", help="Run qlib dump in single-thread mode")

    su = sub.add_parser("stage-update")
    su.add_argument("trade_dates", nargs="+")
    du = sub.add_parser("dump-update")
    du.add_argument("--single-thread", action="store_true", help="Run qlib dump in single-thread mode")
    ts = sub.add_parser("train-select")
    ts.add_argument("--train", nargs=2, metavar=("START", "END"), help="train range: YYYY-MM-DD")
    ts.add_argument("--valid", nargs=2, metavar=("START", "END"), help="valid range: YYYY-MM-DD")
    ts.add_argument("--test", nargs=2, metavar=("START", "END"), help="test range: YYYY-MM-DD")
    ts.add_argument(
        "--benchmark",
        help=(
            "Optional benchmark code, e.g. SH000300. "
            "If omitted, or if specified benchmark is invalid/unavailable, it will auto-try common indexes "
            "(SH000300, SH000905, SZ399001, SZ399006) from qlib or tushare; "
            "if none available it finally falls back to zero return benchmark."
        ),
    )
    return p


def main() -> None:
    args = parser().parse_args()
    settings = Settings.load(args.config)
    ext = Extractor(settings)

    if args.command == "init-metadata":
        ext.fetch_stock_master()
        ext.fetch_calendar(settings.data["start_date"], settings.data["end_date"])
    elif args.command == "backfill":
        ext.backfill(args.start or settings.data["start_date"], args.end or settings.data["end_date"], args.force)
    elif args.command == "curate":
        build_all_curated(settings, args.start, args.end)
    elif args.command == "curate-day":
        build_curated_day(settings, args.trade_date, args.force)
    elif args.command == "stage-full":
        export_full_staging(settings, args.force)
    elif args.command == "dump-full":
        dump_full(settings, single_thread=args.single_thread)
    elif args.command == "stage-update":
        export_incremental_staging(settings, args.trade_dates)
    elif args.command == "dump-update":
        dump_update(settings, single_thread=args.single_thread)
    elif args.command == "train-select":
        train = tuple(args.train) if args.train is not None else None
        valid = tuple(args.valid) if args.valid is not None else None
        test = tuple(args.test) if args.test is not None else None
        benchmark = args.benchmark.strip() if args.benchmark else None
        print(train_backtest_select(settings, train=train, valid=valid, test=test, benchmark=benchmark))
    else:
        raise AssertionError(args.command)


if __name__ == "__main__":
    main()
