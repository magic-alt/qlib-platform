from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .settings import Settings


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Auditable Tushare Pro -> Qlib -> execution pipeline")
    p.add_argument("--config", default="configs/pipeline.yaml")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init-metadata")
    b = sub.add_parser("backfill")
    b.add_argument("--start")
    b.add_argument("--end")
    b.add_argument("--force", action="store_true")
    sp = sub.add_parser("source-preflight")
    sp.add_argument("--start")
    sp.add_argument("--end")
    sb = sub.add_parser("sync-benchmark")
    sb.add_argument("--symbol", default="SH000300")
    sb.add_argument("--start")
    sb.add_argument("--end")
    c = sub.add_parser("curate")
    c.add_argument("--start")
    c.add_argument("--end")
    d = sub.add_parser("curate-day")
    d.add_argument("trade_date")
    d.add_argument("--force", action="store_true")
    sf = sub.add_parser("stage-full")
    sf.add_argument("--force", action="store_true")
    df = sub.add_parser("dump-full")
    df.add_argument("--single-thread", action="store_true")
    su = sub.add_parser("stage-update")
    su.add_argument("trade_dates", nargs="+")
    du = sub.add_parser("dump-update")
    du.add_argument("--single-thread", action="store_true")
    ts = sub.add_parser("train-select")
    ts.add_argument("--train", nargs=2, metavar=("START", "END"))
    ts.add_argument("--valid", nargs=2, metavar=("START", "END"))
    ts.add_argument("--test", nargs=2, metavar=("START", "END"))
    ts.add_argument("--benchmark")
    ts.add_argument("--topn", type=int, default=30)
    rr = sub.add_parser("research-run")
    rr.add_argument("--mode", choices=["fixed", "walk-forward"], default="fixed")
    rr.add_argument("--start")
    rr.add_argument("--end")
    rr.add_argument("--benchmark", default="SH000300")
    rr.add_argument("--topn", type=int, default=30)

    tp = sub.add_parser("build-trade-plan")
    tp.add_argument("--execution-config", default="configs/trading_execution_template.yaml")
    tp.add_argument("--selection-file")
    tp.add_argument("--selection-date")
    tp.add_argument("--current-portfolio")
    tp.add_argument("--trade-date")

    le = sub.add_parser("lean-export")
    le.add_argument("target_file")
    le.add_argument("--output-dir")
    le.add_argument("--signal-date")
    le.add_argument("--trade-date")
    le.add_argument("--model-id")
    le.add_argument("--dataset-id")

    rg = sub.add_parser("research-gate")
    rg.add_argument("metrics_json")
    rg.add_argument("--output")
    ra = sub.add_parser("research-audit")
    ra.add_argument("run_dir")
    ra.add_argument("--output")
    lr = sub.add_parser("lean-register")
    lr.add_argument("manifest")
    lr.add_argument("--base-url")

    pa = sub.add_parser("project-audit")
    pa.add_argument("--root", default=".")
    pa.add_argument("--output", default="docs/project_audit.json")

    eo = sub.add_parser("build-orders")
    eo.add_argument("targets")
    eo.add_argument("positions")
    eo.add_argument("quotes")
    eo.add_argument("--trade-date", required=True)
    eo.add_argument("--portfolio-value", type=float, required=True)
    eo.add_argument("--cash", type=float, required=True)
    eo.add_argument("--output-dir", default="./data/output")
    return p


def _first_value(frame: pd.DataFrame, column: str, fallback: str | None) -> str:
    if fallback:
        return fallback
    if column in frame and frame[column].notna().any():
        return str(frame[column].dropna().iloc[0])
    raise ValueError(f"{column} must be supplied in file or CLI")


def main() -> None:
    args = parser().parse_args()
    if args.command == "project-audit":
        from .project_audit import audit_project, write_audit

        report = audit_project(args.root)
        path = write_audit(report, args.output)
        print(json.dumps({"score": report["score"], "passed": report["passed"], "report": str(path)}, ensure_ascii=False))
        return

    if args.command == "research-audit":
        from .backtest_audit import audit_mlflow_run, write_audit

        report = audit_mlflow_run(args.run_dir)
        path = write_audit(report, args.output)
        print(json.dumps({"passed": report["passed"], "report": str(path)}, ensure_ascii=False))
        if not report["passed"]:
            raise SystemExit(2)
        return

    if args.command == "lean-register":
        from .lean_integration import register_manifest

        print(json.dumps(register_manifest(args.manifest, base_url=args.base_url), ensure_ascii=False))
        return

    if args.command == "build-trade-plan":
        from .trade_plan import build_trade_plan

        path, plan = build_trade_plan(
            config_path=args.execution_config,
            selection_file=args.selection_file,
            selection_date=args.selection_date,
            prev_selection_file=args.current_portfolio,
            trade_date=args.trade_date,
        )
        print(json.dumps({"file": str(path), "rows": len(plan)}, ensure_ascii=False))
        return

    if args.command == "research-gate":
        import yaml
        from .research_gate import ResearchThresholds, evaluate_research_metrics, write_gate_report

        metrics = json.loads(Path(args.metrics_json).read_text(encoding="utf-8"))
        cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
        research = cfg.get("research", {}) if isinstance(cfg, dict) else {}
        thresholds = ResearchThresholds.from_mapping(research.get("promotion_thresholds", {}) if isinstance(research, dict) else {})
        report = evaluate_research_metrics(metrics, thresholds)
        output = args.output or "docs/research_gate.json"
        print(write_gate_report(report, output))
        if not report["passed"]:
            raise SystemExit(2)
        return

    if args.command == "lean-export":
        from .lean_bridge import export_lean_targets

        frame = pd.read_csv(args.target_file)
        signal_date = _first_value(frame, "signal_date", args.signal_date)
        trade_date = _first_value(frame, "trade_date", args.trade_date)
        model_id = _first_value(frame, "model_id", args.model_id or "unversioned")
        dataset_id = _first_value(frame, "dataset_id", args.dataset_id or "unversioned")
        out = args.output_dir or (Path(args.target_file).resolve().parent / "lean")
        print(export_lean_targets(frame, out, signal_date=signal_date, trade_date=trade_date, model_id=model_id, dataset_id=dataset_id))
        return

    if args.command == "build-orders":
        import yaml
        from .execution import ExecutionPolicy, build_orders

        cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
        execution = cfg.get("execution", {}) if isinstance(cfg, dict) else {}
        policy = ExecutionPolicy.from_mapping(execution if isinstance(execution, dict) else {})
        orders, blocked = build_orders(
            pd.read_csv(args.targets), pd.read_csv(args.positions), pd.read_csv(args.quotes),
            trade_date=args.trade_date, portfolio_value=args.portfolio_value, cash=args.cash, policy=policy,
        )
        out = Path(args.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        orders.to_csv(out / f"orders_{args.trade_date.replace('-', '')}.csv", index=False)
        blocked.to_csv(out / f"blocked_orders_{args.trade_date.replace('-', '')}.csv", index=False)
        return

    settings = Settings.load(args.config, require_tushare=False, require_qlib_repo=args.command in {"dump-full", "dump-update"})

    if args.command in {"init-metadata", "backfill", "source-preflight", "sync-benchmark"}:
        from .extract import Extractor

        ext = Extractor(settings)
        if args.command == "init-metadata":
            ext.fetch_stock_master()
            ext.fetch_calendar(settings.data["start_date"], settings.data.get("calendar_end_date", settings.data["end_date"]))
        elif args.command == "backfill":
            ext.backfill(args.start or settings.data["start_date"], args.end or settings.data["end_date"], args.force)
        elif args.command == "source-preflight":
            print(json.dumps(ext.source_preflight(args.start or settings.data["start_date"], args.end or settings.data["end_date"]), ensure_ascii=False, default=str))
        else:
            frame = ext.sync_benchmark(args.symbol, args.start or settings.data["start_date"], args.end or settings.data["end_date"])
            print(json.dumps({"symbol": args.symbol, "rows": len(frame)}, ensure_ascii=False))
    elif args.command in {"curate", "curate-day", "stage-full", "stage-update"}:
        from .normalize import build_all_curated, build_curated_day, export_full_staging, export_incremental_staging

        if args.command == "curate":
            build_all_curated(settings, args.start, args.end)
        elif args.command == "curate-day":
            build_curated_day(settings, args.trade_date, args.force)
        elif args.command == "stage-full":
            export_full_staging(settings, args.force)
        else:
            export_incremental_staging(settings, args.trade_dates)
    elif args.command in {"dump-full", "dump-update"}:
        from .qlib_export import dump_full, dump_update

        path = dump_full(settings, single_thread=args.single_thread) if args.command == "dump-full" else dump_update(settings, single_thread=args.single_thread)
        print(path)
    elif args.command in {"train-select", "research-run"}:
        from .train_select import train_backtest_select

        if args.command == "research-run" and args.mode == "walk-forward":
            from .walk_forward import run_walk_forward

            print(run_walk_forward(settings, start_date=args.start or settings.data["start_date"], end_date=args.end or settings.data["end_date"], benchmark=args.benchmark, topn=args.topn))
        elif args.command == "research-run":
            print(train_backtest_select(settings, benchmark=args.benchmark, topn=args.topn))
        else:
            train = tuple(args.train) if args.train else None
            valid = tuple(args.valid) if args.valid else None
            test = tuple(args.test) if args.test else None
            print(train_backtest_select(settings, train=train, valid=valid, test=test, benchmark=args.benchmark, topn=args.topn))
    else:
        raise AssertionError(args.command)


if __name__ == "__main__":
    main()
