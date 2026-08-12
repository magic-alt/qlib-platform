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
    su_members = sub.add_parser("sync-universe")
    su_members.add_argument("--start")
    su_members.add_argument("--end")
    daily_sync = sub.add_parser("daily-sync")
    daily_sync.add_argument("--as-of")
    daily_sync.add_argument("--check-only", action="store_true")
    daily_sync.add_argument("--force-full", action="store_true")
    dividends = sub.add_parser("sync-dividends")
    dividends.add_argument("--bootstrap", action="store_true")
    dividends.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    kline = sub.add_parser("export-kline")
    kline.add_argument("--symbol", required=True)
    kline.add_argument("--adjust", choices=["raw", "qfq", "hfq"], default="raw")
    kline.add_argument("--start")
    kline.add_argument("--end")
    kline.add_argument("--output", required=True)
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
    fs = sub.add_parser("feature-store")
    fs.add_argument("--start")
    fs.add_argument("--end")
    fs.add_argument("--force", action="store_true")
    runtime_probe = sub.add_parser("runtime-probe")
    runtime_probe.add_argument("--model-profile", required=True)
    ts = sub.add_parser("train-select")
    ts.add_argument("--train", nargs=2, metavar=("START", "END"))
    ts.add_argument("--valid", nargs=2, metavar=("START", "END"))
    ts.add_argument("--test", nargs=2, metavar=("START", "END"))
    ts.add_argument("--benchmark")
    ts.add_argument("--topn", type=int)
    ts.add_argument("--model-profile")
    ts.add_argument("--stage", choices=["signal", "release"], default="release")
    ts.add_argument("--artifact-level", choices=["minimal", "full"], default="full")
    rr = sub.add_parser("research-run")
    rr.add_argument("--mode", choices=["fixed", "walk-forward"], default="fixed")
    rr.add_argument("--start")
    rr.add_argument("--end")
    rr.add_argument("--benchmark", default="SH000300")
    rr.add_argument("--topn", type=int)
    rr.add_argument("--model-profile")
    rr.add_argument("--stage", choices=["signal", "release"], default="release")
    rr.add_argument("--artifact-level", choices=["minimal", "full"], default="full")
    pb = sub.add_parser("backtest-predictions")
    pb.add_argument("predictions")
    pb.add_argument("--benchmark")
    pb.add_argument("--topn", type=int)
    pb.add_argument("--artifact-level", choices=["minimal", "full"], default="minimal")
    rp = sub.add_parser("research-report")
    rp.add_argument("run_dir")
    rp.add_argument("--positions-file")

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
    wc = sub.add_parser("validate-qrun-contract")
    wc.add_argument("--workflow", default="configs/workflow_lightgbm.yaml")

    eo = sub.add_parser("build-orders")
    eo.add_argument("targets")
    eo.add_argument("positions")
    eo.add_argument("quotes")
    eo.add_argument("--trade-date", required=True)
    eo.add_argument("--portfolio-value", type=float, required=True)
    eo.add_argument("--cash", type=float, required=True)
    eo.add_argument("--daily-pnl-pct", type=float, required=True)
    eo.add_argument("--output-dir", default="./data/output")

    pr = sub.add_parser("pretrade-risk")
    pr.add_argument("targets")
    pr.add_argument("--daily-pnl-pct", type=float, required=True)

    be = sub.add_parser("record-broker-event")
    be.add_argument("ledger")
    be.add_argument("order_id")
    be.add_argument("state")
    be.add_argument("--event-at-utc", required=True)
    be.add_argument("--event-id")
    be.add_argument("--broker-order-id")
    be.add_argument("--fill-qty", type=float)
    be.add_argument("--fill-price", type=float)
    fi = sub.add_parser("ingest-pit-fundamentals")
    fi.add_argument("reports")
    fi.add_argument("--calendar")
    fi.add_argument("--output")

    rh = sub.add_parser("reconcile-holdings")
    rh.add_argument("positions")
    rh.add_argument("--fills")
    rh.add_argument("--as-of-date", required=True)
    rh.add_argument("--initial-holdings")
    rh.add_argument("--ledger-path")
    rh.add_argument("--output-dir", default="./data/output")

    to = sub.add_parser("build-topk-orders")
    to.add_argument("signal_file")
    to.add_argument("positions")
    to.add_argument("quotes")
    to.add_argument("--trade-date")
    to.add_argument("--cash", type=float, required=True)
    to.add_argument("--daily-pnl-pct", type=float, required=True)
    to.add_argument("--output-dir", default="./data/output")

    model_refit = sub.add_parser("model-refit")
    model_refit.add_argument("--research-run", required=True)
    model_refit.add_argument("--as-of", required=True)
    model_deploy = sub.add_parser("model-deploy")
    model_deploy.add_argument("deployment_id")
    model_deploy.add_argument("--device", default="cpu")
    model_rollback = sub.add_parser("model-rollback")
    model_rollback.add_argument("--to", required=True, dest="deployment_id")
    model_rollback.add_argument("--device", default="cpu")
    sub.add_parser("model-status")
    live = sub.add_parser("live-inference")
    live.add_argument("--as-of", required=True)
    live.add_argument("--deployment-id")
    live.add_argument("--require-daily-sync", action="store_true")
    live.add_argument("--supersede", action="store_true")
    daily_signal = sub.add_parser("daily-signal-run")
    daily_signal.add_argument("--as-of", required=True)
    daily_signal.add_argument("--no-notify", action="store_true")
    daily_signal.add_argument("--skip-sync", action="store_true")
    daily_signal.add_argument("--supersede", action="store_true")
    daily_action = sub.add_parser("daily-action-run")
    daily_action.add_argument("--trade-date", required=True)
    daily_action.add_argument("--no-notify", action="store_true")
    replay = sub.add_parser("production-replay")
    replay.add_argument("--start", required=True)
    replay.add_argument("--end", required=True)
    replay.add_argument("--deployment-id")
    replay.add_argument("--with-pretrade", action="store_true")
    return p


def _first_value(frame: pd.DataFrame, column: str, fallback: str | None) -> str:
    if fallback:
        return fallback
    if column in frame and frame[column].notna().any():
        return str(frame[column].dropna().iloc[0])
    raise ValueError(f"{column} must be supplied in file or CLI")


def _report_payload(manifest_path: Path, latest_selection: Path | None = None) -> dict[str, object]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifacts = {
        str(item.get("name")): str(item.get("localPath"))
        for item in manifest.get("artifacts", [])
        if isinstance(item, dict) and item.get("name") and item.get("localPath")
    }
    payload: dict[str, object] = {
        "runId": str(manifest.get("externalRunId", manifest_path.parent.name)),
        "timingsJson": artifacts.get("timings.json", str(manifest_path.parent / "timings.json")),
    }
    for artifact_name, payload_key in (
        ("backtest_report.md", "reportMarkdown"),
        ("backtest_report.pdf", "reportPdf"),
    ):
        fallback = manifest_path.parent / artifact_name
        if artifact_name in artifacts:
            payload[payload_key] = artifacts[artifact_name]
        elif fallback.is_file():
            payload[payload_key] = str(fallback)
    if latest_selection is not None:
        payload["latestSelection"] = str(latest_selection)
    runtime = manifest.get("runtime", {})
    timings = manifest.get("timings", {})
    if isinstance(runtime, dict) and runtime:
        payload["modelProfile"] = runtime.get("modelProfile", "unknown")
        payload["resolvedDevice"] = runtime.get("resolvedDevice", "unknown")
    if isinstance(timings, dict) and timings:
        payload["timings"] = timings
    return payload


def main() -> None:
    args = parser().parse_args()
    if args.command == "project-audit":
        from .project_audit import audit_project, write_audit as write_project_audit

        report = audit_project(args.root)
        path = write_project_audit(report, args.output)
        print(
            json.dumps(
                {"score": report["score"], "passed": report["passed"], "report": str(path)},
                ensure_ascii=False,
            )
        )
        return

    if args.command == "validate-qrun-contract":
        from .workflow_contract import validate_qrun_contract

        settings = Settings.load(args.config, create_dirs=False)
        result = validate_qrun_contract(settings, args.workflow)
        print(json.dumps(result, ensure_ascii=False))
        if not result["passed"]:
            raise SystemExit(2)
        return

    if args.command == "research-audit":
        from .backtest_audit import audit_mlflow_run, write_audit as write_backtest_audit

        report = audit_mlflow_run(args.run_dir)
        path = write_backtest_audit(report, args.output)
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
        thresholds = ResearchThresholds.from_mapping(
            research.get("promotion_thresholds", {}) if isinstance(research, dict) else {}
        )
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
        print(
            export_lean_targets(
                frame,
                out,
                signal_date=signal_date,
                trade_date=trade_date,
                model_id=model_id,
                dataset_id=dataset_id,
            )
        )
        return

    if args.command == "build-orders":
        from dataclasses import asdict

        from .canonical_config import ExecutionSpec
        from .execution import ExecutionPolicy, build_orders

        execution_settings = Settings.load(args.config, create_dirs=False)
        policy = ExecutionPolicy.from_mapping(asdict(ExecutionSpec.from_settings(execution_settings)))
        orders, blocked = build_orders(
            pd.read_csv(args.targets),
            pd.read_csv(args.positions),
            pd.read_csv(args.quotes),
            trade_date=args.trade_date,
            portfolio_value=args.portfolio_value,
            cash=args.cash,
            policy=policy,
            daily_pnl_pct=args.daily_pnl_pct,
        )
        out = Path(args.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        orders.to_csv(out / f"orders_{args.trade_date.replace('-', '')}.csv", index=False)
        blocked.to_csv(out / f"blocked_orders_{args.trade_date.replace('-', '')}.csv", index=False)
        return

    if args.command == "pretrade-risk":
        from .risk_engine import HardRiskPolicy, pretrade_risk_check

        artifact = pd.read_csv(args.targets)
        # The target artifact's manifest is the release's policy authority.
        from .artifacts import ArtifactType, load_artifact_manifest, validate_artifact

        metadata = validate_artifact(artifact, ArtifactType.TARGET_PORTFOLIO)
        manifest = load_artifact_manifest(metadata)
        canonical = manifest.get("canonicalConfig", {})
        risk = canonical.get("risk", {}) if isinstance(canonical, dict) else {}
        print(
            json.dumps(
                pretrade_risk_check(
                    artifact, HardRiskPolicy.from_mapping(risk), daily_pnl_pct=args.daily_pnl_pct
                )
            )
        )
        return

    if args.command == "record-broker-event":
        from .broker_state import record_broker_event

        events = record_broker_event(
            args.ledger,
            args.order_id,
            args.state,
            event_at_utc=args.event_at_utc,
            event_id=args.event_id,
            broker_order_id=args.broker_order_id,
            fill_qty=args.fill_qty,
            fill_price=args.fill_price,
        )
        print(json.dumps({"ledger": str(args.ledger), "events": len(events)}))
        return

    if args.command == "ingest-pit-fundamentals":
        from .fundamentals import ingest_pit_fundamentals

        pit_settings = Settings.load(args.config, create_dirs=False)
        calendar = args.calendar or str(pit_settings.paths.metadata / "trade_calendar.parquet")
        output = args.output or str(pit_settings.paths.curated / "fundamentals_pit.parquet")
        print(ingest_pit_fundamentals(args.reports, calendar, output))
        return

    # Qlib export resolves the checkout that actually supplies the imported
    # package, so a stale optional QLIB_REPO does not mask a valid editable
    # installation before export can validate it.
    settings = Settings.load(args.config, require_tushare=False)

    if args.command == "model-refit":
        from .production_refit import refit_production_model

        path = refit_production_model(settings, args.research_run, as_of=args.as_of)
        print(json.dumps({"manifest": str(path)}, ensure_ascii=False))
        return
    if args.command in {"model-deploy", "model-rollback", "model-status"}:
        from .model_registry import ModelRegistry

        registry = ModelRegistry(settings)
        if args.command == "model-deploy":
            registry_result = registry.deploy(args.deployment_id, device=args.device)
        elif args.command == "model-rollback":
            registry_result = registry.rollback(args.deployment_id, device=args.device)
        else:
            registry_result = registry.current()
        registry_result.pop("metadata_json", None)
        print(json.dumps(registry_result, ensure_ascii=False))
        return
    if args.command == "live-inference":
        from .live_inference import run_live_inference

        live_result = run_live_inference(
            settings,
            as_of=args.as_of,
            deployment_id=args.deployment_id,
            require_daily_sync=args.require_daily_sync,
            supersede=args.supersede,
        )
        print(
            json.dumps(
                {
                    "signalId": live_result.signal_id,
                    "manifest": str(live_result.manifest_path),
                    "health": live_result.health.to_dict(),
                },
                ensure_ascii=False,
            )
        )
        if not live_result.health.passed:
            raise SystemExit(2)
        return
    if args.command == "daily-signal-run":
        from .daily_signal_runner import run_daily_signal

        daily_result = run_daily_signal(
            settings,
            as_of=args.as_of,
            notify=not args.no_notify,
            skip_sync=args.skip_sync,
            supersede=args.supersede,
        )
        print(json.dumps({"signalId": daily_result.signal_id, "manifest": str(daily_result.manifest_path)}))
        return
    if args.command == "daily-action-run":
        from .pretrade_runner import run_pretrade_actions

        action_result = run_pretrade_actions(
            settings, trade_date=args.trade_date, notify=not args.no_notify
        )
        print(
            json.dumps(
                {
                    "signalId": action_result.signal_id,
                    "decision": str(action_result.decision_path),
                    "orders": str(action_result.orders_path),
                    "blocked": str(action_result.blocked_path),
                },
                ensure_ascii=False,
            )
        )
        return
    if args.command == "production-replay":
        from .production_replay import run_production_replay

        replay_path = run_production_replay(
            settings,
            start=args.start,
            end=args.end,
            deployment_id=args.deployment_id,
            with_pretrade=args.with_pretrade,
        )
        replay_payload = json.loads(replay_path.read_text(encoding="utf-8"))
        print(json.dumps({"report": str(replay_path), "passed": replay_payload["passed"]}, ensure_ascii=False))
        if not replay_payload["passed"]:
            raise SystemExit(2)
        return

    if args.command == "daily-sync":
        from .daily_sync import run_daily_sync

        sync_manifest_path = run_daily_sync(
            settings,
            as_of=args.as_of,
            check_only=args.check_only,
            force_full=args.force_full,
        )
        print(json.dumps(json.loads(sync_manifest_path.read_text(encoding="utf-8")), ensure_ascii=False))
        return

    if args.command == "sync-dividends":
        if not args.bootstrap:
            raise ValueError("sync-dividends currently requires --bootstrap; daily deltas use daily-sync")
        from .corporate_actions import CorporateActionStore
        from .extract import Extractor

        extractor = Extractor(settings)
        master_path = settings.paths.metadata / "stock_master.parquet"
        master = pd.read_parquet(master_path) if master_path.is_file() else extractor.fetch_stock_master()
        result = CorporateActionStore(settings).bootstrap(
            extractor.client,
            master,
            resume=args.resume,
        )
        print(json.dumps(result, ensure_ascii=False))
        return

    if args.command == "export-kline":
        from .kline_export import export_kline

        path = export_kline(
            settings,
            args.symbol,
            args.output,
            start_date=args.start,
            end_date=args.end,
            adjustment=args.adjust,
        )
        print(path)
        return

    if args.command == "research-report":
        from .backtest_report import write_backtest_report

        run_dir = Path(args.run_dir).expanduser().resolve()
        write_backtest_report(settings, run_dir, positions_file=args.positions_file)
        print(json.dumps(_report_payload(run_dir / "manifest.json"), ensure_ascii=False))
        return

    if args.command == "backtest-predictions":
        from .prediction_backtest import backtest_predictions

        manifest_path = backtest_predictions(
            settings,
            args.predictions,
            benchmark=args.benchmark,
            topn=args.topn,
            artifact_level=args.artifact_level,
        )
        print(json.dumps(_report_payload(manifest_path), ensure_ascii=False))
        return

    if args.command == "reconcile-holdings":
        from .holdings_ledger import reconcile_holdings

        ledger = (
            Path(args.ledger_path).expanduser().resolve()
            if args.ledger_path
            else settings.paths.root / "state" / "topk_holdings.parquet"
        )
        state = reconcile_holdings(
            pd.read_csv(args.positions),
            pd.read_csv(args.fills) if args.fills else None,
            as_of_date=args.as_of_date,
            calendar_path=settings.paths.metadata / "trade_calendar.parquet",
            ledger_path=ledger,
            initial_holdings=pd.read_csv(args.initial_holdings) if args.initial_holdings else None,
        )
        out = Path(args.output_dir).expanduser().resolve()
        out.mkdir(parents=True, exist_ok=True)
        key = pd.Timestamp(args.as_of_date).strftime("%Y%m%d")
        state.to_csv(out / f"holdings_state_{key}.csv", index=False)
        print(
            json.dumps(
                {"rows": len(state), "ledger": str(ledger), "state": str(out / f"holdings_state_{key}.csv")},
                ensure_ascii=False,
            )
        )
        return

    if args.command == "build-topk-orders":
        from dataclasses import asdict

        from .canonical_config import ExecutionSpec, StrategySpec
        from .execution import ExecutionPolicy, build_topk_orders

        signal = pd.read_parquet(args.signal_file)
        required = {"signal_date", "trade_date", "instrument", "score"}
        missing = required - set(signal.columns)
        if missing:
            raise ValueError(f"signal_file missing columns: {sorted(missing)}")
        signal_dates = pd.to_datetime(signal["signal_date"], errors="raise").dt.normalize().unique()
        trade_dates = pd.to_datetime(signal["trade_date"], errors="raise").dt.normalize().unique()
        if len(signal_dates) != 1 or len(trade_dates) != 1:
            raise ValueError("signal_file must contain exactly one signal_date and trade_date")
        signal_date = pd.Timestamp(signal_dates[0]).strftime("%Y-%m-%d")
        implied_trade_date = pd.Timestamp(trade_dates[0]).strftime("%Y-%m-%d")
        trade_date = args.trade_date or implied_trade_date
        if pd.Timestamp(trade_date).normalize() != pd.Timestamp(implied_trade_date).normalize():
            raise ValueError("--trade-date must match the signal artifact's trade_date")
        strategy_policy = StrategySpec.from_settings(settings).to_policy()
        artifact_policy_columns = {
            "topk": "strategy_topk",
            "n_drop": "strategy_n_drop",
            "hold_thresh": "strategy_hold_thresh",
            "risk_degree": "strategy_risk_degree",
            "only_tradable": "strategy_only_tradable",
            "forbid_all_trade_at_limit": "strategy_forbid_all_trade_at_limit",
        }
        if set(artifact_policy_columns.values()).issubset(signal.columns):
            artifact_policy = {
                key: signal[column].dropna().iloc[0] for key, column in artifact_policy_columns.items()
            }
            if artifact_policy != strategy_policy.__dict__:
                raise ValueError("signal artifact strategy does not match the canonical strategy config")
        execution_policy = ExecutionPolicy.from_mapping(asdict(ExecutionSpec.from_settings(settings)))
        decision, orders, blocked = build_topk_orders(
            signal,
            pd.read_csv(args.positions),
            pd.read_csv(args.quotes),
            signal_date=signal_date,
            trade_date=trade_date,
            cash=args.cash,
            strategy_policy=strategy_policy,
            execution_policy=execution_policy,
            daily_pnl_pct=args.daily_pnl_pct,
        )
        out = Path(args.output_dir).expanduser().resolve()
        out.mkdir(parents=True, exist_ok=True)
        key = pd.Timestamp(trade_date).strftime("%Y%m%d")
        decision.to_csv(out / f"strategy_decision_{key}.csv", index=False)
        orders.to_csv(out / f"orders_{key}.csv", index=False)
        blocked.to_csv(out / f"blocked_orders_{key}.csv", index=False)
        print(
            json.dumps(
                {"decision_rows": len(decision), "orders": len(orders), "blocked": len(blocked)},
                ensure_ascii=False,
            )
        )
        return

    if args.command in {
        "init-metadata",
        "backfill",
        "source-preflight",
        "sync-benchmark",
        "sync-universe",
    }:
        from .extract import Extractor

        ext = Extractor(settings)
        if args.command == "init-metadata":
            ext.fetch_stock_master()
            ext.fetch_calendar(
                settings.data["start_date"], settings.data.get("calendar_end_date", settings.data["end_date"])
            )
        elif args.command == "backfill":
            ext.backfill(
                args.start or settings.data["start_date"], args.end or settings.data["end_date"], args.force
            )
        elif args.command == "source-preflight":
            result = ext.source_preflight(
                args.start or settings.data["start_date"], args.end or settings.data["end_date"]
            )
            print(
                json.dumps(
                    result,
                    ensure_ascii=False,
                    default=str,
                )
            )
            if not result.get("passed"):
                raise SystemExit(2)
        elif args.command == "sync-benchmark":
            frame = ext.sync_benchmark(
                args.symbol, args.start or settings.data["start_date"], args.end or settings.data["end_date"]
            )
            print(json.dumps({"symbol": args.symbol, "rows": len(frame)}, ensure_ascii=False))
        else:
            frame = ext.sync_universe_membership(
                args.start or settings.data["start_date"], args.end or settings.data["end_date"]
            )
            print(json.dumps({"intervals": len(frame)}, ensure_ascii=False))
    elif args.command in {"curate", "curate-day", "stage-full", "stage-update"}:
        from .normalize import (
            build_all_curated,
            build_curated_day,
            export_full_staging,
            export_incremental_staging,
        )

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

        path = (
            dump_full(settings, single_thread=args.single_thread)
            if args.command == "dump-full"
            else dump_update(settings, single_thread=args.single_thread)
        )
        print(path)
    elif args.command == "runtime-probe":
        from .model_runtime import load_model_profile, resolve_runtime

        runtime = resolve_runtime(load_model_profile(settings, args.model_profile))
        print(json.dumps(runtime.to_manifest(), ensure_ascii=False))
    elif args.command == "feature-store":
        from .feature_store import prepare_feature_data

        _, feature_metadata = prepare_feature_data(
            settings,
            args.start or settings.data["start_date"],
            args.end or settings.data["end_date"],
            force=args.force,
        )
        print(json.dumps(feature_metadata, ensure_ascii=False))
    elif args.command in {"train-select", "research-run"}:
        from .train_select import train_backtest_select

        if args.command == "research-run" and args.mode == "walk-forward":
            if args.stage != "release":
                raise ValueError("walk-forward currently requires --stage release")
            from .walk_forward import run_walk_forward

            walk_manifest_path = run_walk_forward(
                settings,
                start_date=args.start or settings.data["start_date"],
                end_date=args.end or settings.data["end_date"],
                benchmark=args.benchmark,
                topn=args.topn,
                model_profile=args.model_profile,
            )
            print(json.dumps(_report_payload(walk_manifest_path), ensure_ascii=False))
        elif args.command == "research-run":
            research_result = train_backtest_select(
                settings,
                benchmark=args.benchmark,
                topn=args.topn,
                model_profile=args.model_profile,
                promotion_mode="signal" if args.stage == "signal" else "release",
                artifact_level=args.artifact_level,
            )
            if research_result.name == "manifest.json":
                print(json.dumps(_report_payload(research_result), ensure_ascii=False))
            else:
                model_id = str(pd.read_csv(research_result)["model_id"].iloc[0])
                research_manifest_path = settings.paths.output / "research" / model_id / "manifest.json"
                print(json.dumps(_report_payload(research_manifest_path, research_result), ensure_ascii=False))
        else:
            train = tuple(args.train) if args.train else None
            valid = tuple(args.valid) if args.valid else None
            test = tuple(args.test) if args.test else None
            selection = train_backtest_select(
                settings,
                train=train,
                valid=valid,
                test=test,
                benchmark=args.benchmark,
                topn=args.topn,
                model_profile=args.model_profile,
                promotion_mode="signal" if args.stage == "signal" else "release",
                artifact_level=args.artifact_level,
            )
            if selection.name == "manifest.json":
                print(json.dumps(_report_payload(selection), ensure_ascii=False))
            else:
                model_id = str(pd.read_csv(selection)["model_id"].iloc[0])
                manifest_path = settings.paths.output / "research" / model_id / "manifest.json"
                print(json.dumps(_report_payload(manifest_path, selection), ensure_ascii=False))
    else:
        raise AssertionError(args.command)


if __name__ == "__main__":
    main()
