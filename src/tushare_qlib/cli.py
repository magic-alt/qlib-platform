from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from .settings import Settings


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Auditable platform DataRelease -> Qlib research pipeline")
    p.add_argument("--config", default="configs/pipeline.yaml")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init-metadata")
    b = sub.add_parser("backfill")
    b.add_argument("--start")
    b.add_argument("--end")
    b.add_argument("--force", action="store_true")
    extended = sub.add_parser("backfill-extended")
    extended.add_argument("--start")
    extended.add_argument("--end")
    extended.add_argument("--groups", nargs="+", help="data domains; defaults to all extended domains")
    extended.add_argument("--force", action="store_true")
    extended.add_argument("--workers", type=int, help="parallel workers for per-symbol history endpoints")
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
    fs.add_argument("--dataset-ref")
    dataset_build = sub.add_parser("dataset-build")
    dataset_build.add_argument("--start")
    dataset_build.add_argument("--end")
    dataset_build.add_argument("--single-thread", action="store_true")
    migration = sub.add_parser("migrate-qlib-layout")
    migration.add_argument("--apply", action="store_true")
    migration.add_argument("--migration-id")
    dataset_list = sub.add_parser("dataset-list")
    dataset_list.add_argument("--name")
    dataset_show = sub.add_parser("dataset-show")
    dataset_show.add_argument("reference")
    dataset_verify = sub.add_parser("dataset-verify")
    dataset_verify.add_argument("reference")
    dataset_verify.add_argument("--metadata-only", action="store_true")
    dataset_resolve = sub.add_parser("dataset-resolve")
    dataset_resolve.add_argument("reference", nargs="?")
    dataset_promote = sub.add_parser("dataset-promote")
    dataset_promote.add_argument("reference")
    dataset_promote.add_argument("--alias", default="research-current")
    registry_rebuild = sub.add_parser("registry-rebuild")
    registry_rebuild.add_argument("--root")
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
    ts.add_argument("--dataset-ref")
    rr = sub.add_parser("research-run")
    rr.add_argument("--mode", choices=["fixed", "walk-forward"], default="fixed")
    rr.add_argument("--start")
    rr.add_argument("--end")
    rr.add_argument("--benchmark", default="SH000300")
    rr.add_argument("--topn", type=int)
    rr.add_argument("--model-profile")
    rr.add_argument("--stage", choices=["signal", "release"], default="release")
    rr.add_argument("--artifact-level", choices=["minimal", "full"], default="full")
    rr.add_argument("--dataset-ref")
    rr.add_argument("--full-acceptance", action="store_true")
    rr.add_argument("--interrupt-after-fold", type=int)
    rr.add_argument("--checkpoint-namespace", default="default")
    rr.add_argument("--feature-set")
    rr.add_argument("--selected-technical", action="append", default=[])
    rr.add_argument("--hypothesis-id")
    rr.add_argument("--hypothesis-role", choices=["candidate", "baseline"], default="candidate")
    rr.add_argument("--contract-lock")
    pb = sub.add_parser("backtest-predictions")
    pb.add_argument("predictions")
    pb.add_argument("--benchmark")
    pb.add_argument("--topn", type=int)
    pb.add_argument("--n-drop", type=int)
    pb.add_argument("--hold-thresh", type=int)
    pb.add_argument("--artifact-level", choices=["minimal", "full"], default="minimal")
    pb.add_argument("--dataset-ref")
    rp = sub.add_parser("research-report")
    rp.add_argument("run_dir")
    rp.add_argument("--positions-file")
    alpha_diagnose = sub.add_parser("alpha-diagnose")
    alpha_diagnose.add_argument("--acceptance", required=True)
    alpha_diagnose.add_argument("--walk-forward", required=True)
    alpha_diagnose.add_argument("--feature-snapshot", required=True)
    alpha_diagnose.add_argument("--taxonomy", default="configs/alpha_taxonomy/alpha158_pit_v1.yaml")
    alpha_diagnose.add_argument("--output")
    regime_diagnose = sub.add_parser("regime-diagnose")
    regime_diagnose.add_argument("--base-study", required=True)
    regime_diagnose.add_argument("--acceptance", required=True)
    regime_diagnose.add_argument("--walk-forward", required=True)
    regime_diagnose.add_argument("--ridge-predictions", required=True)
    regime_diagnose.add_argument("--lightgbm-predictions", required=True)
    regime_diagnose.add_argument("--feature-snapshot", required=True)
    regime_diagnose.add_argument("--taxonomy", default="configs/alpha_taxonomy/alpha158_pit_v1.yaml")
    regime_diagnose.add_argument("--regimes", default="configs/regimes/ashare_regime_v1.yaml")
    regime_diagnose.add_argument("--output")
    attribution_diagnose = sub.add_parser("attribution-diagnose")
    attribution_diagnose.add_argument("--regime-study", required=True)
    attribution_diagnose.add_argument("--acceptance", required=True)
    attribution_diagnose.add_argument("--walk-forward", required=True)
    attribution_diagnose.add_argument("--ridge-predictions", required=True)
    attribution_diagnose.add_argument("--lightgbm-predictions", required=True)
    attribution_diagnose.add_argument(
        "--portfolio-run",
        action="append",
        default=[],
        metavar="MODEL:VARIANT=PATH",
        help="optional certified baseline or bounded prediction-only portfolio input",
    )
    attribution_diagnose.add_argument(
        "--attribution",
        default="configs/attribution/ashare_failure_attribution_v1.yaml",
    )
    attribution_diagnose.add_argument("--output")
    explanation_diagnose = sub.add_parser("explanation-diagnose")
    explanation_diagnose.add_argument("--base-study", required=True)
    explanation_diagnose.add_argument("--regime-study", required=True)
    explanation_diagnose.add_argument("--attribution-study", required=True)
    explanation_diagnose.add_argument("--acceptance", required=True)
    explanation_diagnose.add_argument("--ridge-walk-forward", required=True)
    explanation_diagnose.add_argument("--lightgbm-walk-forward", required=True)
    explanation_diagnose.add_argument("--xgboost-walk-forward", required=True)
    explanation_diagnose.add_argument("--feature-snapshot", required=True)
    explanation_diagnose.add_argument("--taxonomy", default="configs/alpha_taxonomy/alpha158_pit_v1.yaml")
    explanation_diagnose.add_argument(
        "--model-artifact-root",
        action="append",
        required=True,
        metavar="PATH",
        help="local MLflow/Qlib recorder root containing RUN_ID/artifacts/params.pkl",
    )
    explanation_diagnose.add_argument(
        "--explanation",
        default="configs/explanation/ashare_model_explanation_v1.yaml",
    )
    explanation_diagnose.add_argument("--output")
    phase1_synthesize = sub.add_parser("phase1-synthesize")
    phase1_synthesize.add_argument("--feature-study", required=True)
    phase1_synthesize.add_argument("--regime-study", required=True)
    phase1_synthesize.add_argument("--attribution-study", required=True)
    phase1_synthesize.add_argument("--explanation-study", required=True)
    phase1_synthesize.add_argument(
        "--synthesis",
        default="configs/synthesis/ashare_phase1_synthesis_v1.yaml",
    )
    phase1_synthesize.add_argument("--output")
    phase2_validate = sub.add_parser("phase2-validate")
    phase2_validate.add_argument("--phase1-manifest", required=True)
    phase2_validate.add_argument(
        "--contract",
        default="configs/research/ashare_phase2_v1.yaml",
    )
    phase2_validate.add_argument("--output", required=True)
    phase2_plan = sub.add_parser("phase2-plan")
    phase2_plan.add_argument("--contract-lock", required=True)
    phase2_plan.add_argument("--output", required=True)
    phase2_data_accept = sub.add_parser("phase2-data-accept")
    phase2_data_accept.add_argument("--evidence", required=True)
    phase2_data_accept.add_argument("--output", required=True)
    phase2_collect = sub.add_parser("phase2-collect")
    phase2_collect.add_argument("--contract-lock", required=True)
    phase2_collect.add_argument("--evidence", required=True)
    phase2_collect.add_argument("--output", required=True)
    phase2_accept = sub.add_parser("phase2-accept")
    phase2_accept.add_argument("--contract-lock", required=True)
    phase2_accept.add_argument("--candidate-metrics", "--candidates", dest="candidate_metrics", required=True)
    phase2_accept.add_argument("--output", required=True)
    phase2_select = sub.add_parser("phase2-select")
    phase2_select.add_argument("--contract-lock", required=True)
    phase2_select.add_argument("--acceptance", required=True)
    phase2_select.add_argument("--design-release", required=True)
    phase2_select.add_argument("--selection-date", required=True)
    phase2_select.add_argument("--output", required=True)
    phase2_holdout = sub.add_parser("phase2-final-holdout-open")
    phase2_holdout.add_argument("--selection-lock", required=True)
    phase2_holdout.add_argument("--final-release", required=True)
    phase2_holdout.add_argument("--calendar", required=True)
    phase2_holdout.add_argument("--output", required=True)
    phase3_validate = sub.add_parser("phase3-validate")
    phase3_validate.add_argument("--phase2-acceptance", required=True)
    phase3_validate.add_argument("--phase2-evidence", required=True)
    phase3_validate.add_argument("--phase2-data-acceptance", required=True)
    phase3_validate.add_argument(
        "--contract",
        default="configs/research/ashare_phase3_v1.yaml",
    )
    phase3_validate.add_argument("--output", required=True)
    phase3_plan = sub.add_parser("phase3-plan")
    phase3_plan.add_argument("--contract-lock", required=True)
    phase3_plan.add_argument("--output", required=True)
    phase3_diagnose = sub.add_parser("phase3-diagnose")
    phase3_diagnose.add_argument("--contract-lock", required=True)
    phase3_diagnose.add_argument("--plan", required=True)
    phase3_diagnose.add_argument("--evidence", required=True)
    phase3_diagnose.add_argument(
        "--regimes",
        default="configs/regimes/ashare_regime_v1.yaml",
    )
    phase3_diagnose.add_argument("--output", required=True)
    phase3_export = sub.add_parser("phase3-portable-export")
    phase3_export.add_argument("--contract-lock", required=True)
    phase3_export.add_argument("--plan", required=True)
    phase3_export.add_argument("--diagnosis", required=True)
    phase3_export.add_argument("--contract", default="configs/research/ashare_phase3_v1.yaml")
    phase3_export.add_argument("--data-root", required=True)
    phase3_export.add_argument("--output", required=True)
    phase3_verify = sub.add_parser("phase3-portable-verify")
    phase3_verify.add_argument("--package", required=True)

    tp = sub.add_parser("build-target-portfolio")
    tp.add_argument("--portfolio-config", default="configs/target_portfolio.yaml")
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
    v2 = sub.add_parser("artifact-v2-export")
    v2.add_argument("manifest")
    v2.add_argument("--output-dir", required=True)
    v2.add_argument("--git-commit", required=True)
    v2.add_argument("--container-digest", required=True)
    v2.add_argument("--data-release-id")

    pa = sub.add_parser("project-audit")
    pa.add_argument("--root", default=".")
    pa.add_argument("--output", default="docs/project_audit.json")
    wc = sub.add_parser("validate-qrun-contract")
    wc.add_argument("--workflow", default="configs/workflow_lightgbm.yaml")

    fi = sub.add_parser("ingest-pit-fundamentals")
    fi.add_argument("reports")
    fi.add_argument("--calendar")
    fi.add_argument("--output")

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
    live.add_argument("--dataset-uri")
    live.add_argument("--dataset-ref")
    live.add_argument("--compare-research")
    live.add_argument("--parity-output")
    daily_signal = sub.add_parser("daily-signal-run")
    daily_signal.add_argument("--as-of", required=True)
    daily_signal.add_argument("--no-notify", action="store_true")
    daily_signal.add_argument("--skip-sync", action="store_true")
    daily_signal.add_argument("--supersede", action="store_true")
    ops_query = sub.add_parser("ops-query", help="query production state")
    ops_query.add_argument("--entity", choices=["runs", "deliveries"], required=True)
    ops_query.add_argument("--business-date")
    ops_query.add_argument("--status")
    ops_retry = sub.add_parser("ops-retry-delivery")
    ops_retry.add_argument("idempotency_key")
    ops_ack = sub.add_parser("ops-ack")
    ops_ack.add_argument("--entity", choices=["run", "delivery"], required=True)
    ops_ack.add_argument("--id", required=True, dest="entity_id")
    ops_ack.add_argument("--operator", required=True)
    ops_ack.add_argument("--reason", required=True)
    ops_summary = sub.add_parser("ops-summary")
    ops_summary.add_argument("--business-date", required=True)
    ops_summary.add_argument("--output")
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
    if args.command == "migrate-qlib-layout":
        from .layout_migration import LayoutMigrator

        migration_settings = Settings.load(args.config, create_dirs=False)
        migrator = LayoutMigrator(migration_settings)
        result = (
            {"journal": str(migrator.apply(args.migration_id)), "applied": True}
            if args.apply
            else migrator.plan()
        )
        print(json.dumps(result, ensure_ascii=False))
        return
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

    if args.command == "artifact-v2-export":
        from .research_bundle_export import export_manifest_as_v2_bundle

        path = export_manifest_as_v2_bundle(
            args.manifest,
            args.output_dir,
            git_commit=args.git_commit,
            container_digest=args.container_digest,
            data_release_id=args.data_release_id,
        )
        print(json.dumps({"manifest": str(path)}, ensure_ascii=False))
        return

    if args.command == "build-target-portfolio":
        from .trade_plan import build_trade_plan

        path, plan = build_trade_plan(
            config_path=args.portfolio_config,
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
    if args.command == "research-run" and (args.feature_set or args.hypothesis_id):
        experiment = settings.data.setdefault("experiment", {})
        if not isinstance(experiment, dict):
            raise ValueError("experiment config must be a mapping")
        alpha = experiment.setdefault("alpha", {})
        if not isinstance(alpha, dict):
            raise ValueError("experiment.alpha config must be a mapping")
        if args.hypothesis_id:
            if args.feature_set or args.selected_technical:
                raise ValueError(
                    "--hypothesis-id cannot be combined with --feature-set or --selected-technical"
                )
            if not args.contract_lock:
                raise ValueError("--hypothesis-id requires --contract-lock")
            if args.mode == "walk-forward":
                raise ValueError(
                    "formal Phase 2 hypotheses must be executed as the frozen rolling-OOS folds; "
                    "generic walk-forward includes final holdout and is forbidden"
                )
            from .research.phase2_hypotheses import bind_phase2_hypothesis

            binding = bind_phase2_hypothesis(
                args.contract_lock,
                args.hypothesis_id,
                args.hypothesis_role,
            )
            alpha["feature_set"] = binding.feature_set_id
            alpha["selected_technical"] = []
            experiment["phase2_hypothesis"] = binding.to_manifest()
        else:
            if args.contract_lock:
                raise ValueError("--contract-lock is only valid with --hypothesis-id")
            alpha["feature_set"] = args.feature_set
            alpha["selected_technical"] = list(args.selected_technical)

    if args.command in {
        "dataset-list",
        "dataset-show",
        "dataset-verify",
        "dataset-resolve",
        "dataset-promote",
        "registry-rebuild",
    }:
        from .dataset_manifest import verify_dataset_manifest
        from .dataset_registry import DatasetRegistry
        from .dataset_resolver import resolve_dataset

        dataset_registry = DatasetRegistry(settings.registry_path)
        dataset_registry.initialize()
        if args.command == "dataset-list":
            versions = dataset_registry.list_versions(args.name)
            print(
                json.dumps(
                    [
                        {
                            "versionId": item.version_id,
                            "datasetName": item.dataset_name,
                            "layer": item.layer,
                            "status": item.status,
                            "dataPath": str(item.data_path),
                            "createdAtUtc": item.created_at_utc,
                        }
                        for item in versions
                    ],
                    ensure_ascii=False,
                )
            )
        elif args.command == "registry-rebuild":
            print(json.dumps({"registered": dataset_registry.rebuild(args.root or settings.paths.root)}))
        elif args.command == "dataset-promote":
            direct = dataset_registry.get_version(args.reference)
            version_id = (
                direct.version_id
                if direct is not None
                else dataset_registry.resolve(args.reference).version_id
            )
            promoted = dataset_registry.promote(args.alias, version_id)
            print(json.dumps({"alias": args.alias, "versionId": promoted.version_id}))
        else:
            resolved = resolve_dataset(settings, getattr(args, "reference", None), allow_legacy=False)
            if args.command == "dataset-resolve":
                print(
                    json.dumps(
                        {
                            "reference": resolved.reference,
                            "versionId": resolved.version_id,
                            "path": str(resolved.data_path),
                        }
                    )
                )
            elif args.command == "dataset-show":
                print(resolved.manifest_path.read_text(encoding="utf-8"))
            else:
                verified = verify_dataset_manifest(
                    resolved.manifest_path, verify_files=not args.metadata_only
                )
                print(json.dumps({"versionId": verified["version_id"], "verified": True}))
        return

    dataset_ref = getattr(args, "dataset_ref", None)
    if dataset_ref:
        from dataclasses import replace
        from .dataset_resolver import resolve_dataset

        resolved = resolve_dataset(settings, dataset_ref, allow_legacy=False)
        settings = replace(settings, qlib_data_uri=resolved.data_path)

    if args.command == "dataset-build":
        import uuid

        from .dataset_registry import DatasetRegistry
        from .fundamentals import build_pit_from_extended
        from .lakehouse import freeze_pipeline_layers
        from .normalize import build_all_curated, export_full_staging
        from .qlib_export import dump_full

        run_id = f"dataset-build-{uuid.uuid4().hex}"
        run_registry = DatasetRegistry(settings.registry_path)
        run_registry.start_pipeline_run(run_id, "dataset_build")
        try:
            if settings.uses_platform_release():
                from .platform_release import materialize_platform_release

                release = materialize_platform_release(settings)
                path = dump_full(
                    settings,
                    single_thread=args.single_thread,
                    sync_context={
                        "data_release_id": release.data_release_id,
                        "data_release_manifest_sha256": release.manifest_sha256,
                        "dataset_parents": [
                            {
                                "version_id": release.data_release_id,
                                "relation": "converted_from",
                            }
                        ],
                    },
                )
            else:
                build_pit_from_extended(settings)
                build_all_curated(settings, args.start, args.end)
                export_full_staging(settings, force=True)
                snapshots = freeze_pipeline_layers(
                    settings,
                    mode="full",
                    gold_sources=(("qlib_input", settings.paths.staging_full),),
                )
                path = dump_full(
                    settings,
                    single_thread=args.single_thread,
                    sync_context={
                        "dataset_parents": [
                            {"version_id": snapshots[-1]["version_id"], "relation": "converted_from"}
                        ]
                    },
                )
        except Exception as exc:
            run_registry.finish_pipeline_run(run_id, status="FAILED", error_code=type(exc).__name__)
            raise
        run_registry.finish_pipeline_run(
            run_id,
            status="SUCCEEDED",
            dataset_version_id=path.name,
            manifest_path=path / "dataset_manifest.json",
        )
        print(json.dumps({"dataset": str(path), "manifest": str(path / "dataset_manifest.json")}))
        return

    if args.command == "ops-query":
        from .ops_cli import query_ops

        print(
            json.dumps(
                query_ops(
                    settings,
                    entity=args.entity,
                    business_date=args.business_date,
                    status=args.status,
                ),
                ensure_ascii=False,
                default=str,
            )
        )
        return
    if args.command == "ops-retry-delivery":
        from .ops_cli import state_from_settings

        state_from_settings(settings).recover_delivery(args.idempotency_key)
        print(json.dumps({"idempotencyKey": args.idempotency_key, "status": "RETRY_READY"}))
        return
    if args.command == "ops-ack":
        from .ops_cli import state_from_settings

        state_from_settings(settings).acknowledge(
            args.entity, args.entity_id, operator=args.operator, reason=args.reason
        )
        print(json.dumps({"entity": args.entity, "id": args.entity_id, "acknowledged": True}))
        return
    if args.command == "ops-summary":
        from .ops_cli import export_daily_ops, state_from_settings

        if args.output:
            path = export_daily_ops(settings, args.business_date, args.output)
            print(json.dumps({"summary": str(path)}, ensure_ascii=False))
        else:
            print(
                json.dumps(
                    state_from_settings(settings).daily_summary(args.business_date),
                    ensure_ascii=False,
                    default=str,
                )
            )
        return
    if args.command == "model-refit":
        from .production_refit import refit_production_model

        path = refit_production_model(settings, args.research_run, as_of=args.as_of)
        print(json.dumps({"manifest": str(path)}, ensure_ascii=False))
        return
    if args.command in {"model-deploy", "model-rollback", "model-status"}:
        from .model_registry import ModelRegistry

        model_registry = ModelRegistry(settings)
        if args.command == "model-deploy":
            registry_result = model_registry.deploy(args.deployment_id, device=args.device)
        elif args.command == "model-rollback":
            registry_result = model_registry.rollback(args.deployment_id, device=args.device)
        else:
            registry_result = model_registry.current()
        registry_result.pop("metadata_json", None)
        print(json.dumps(registry_result, ensure_ascii=False))
        return
    if args.command == "live-inference":
        if args.dataset_uri:
            from dataclasses import replace

            settings = replace(settings, qlib_data_uri=Path(args.dataset_uri).expanduser().resolve())
        from .live_inference import run_live_inference

        live_result = run_live_inference(
            settings,
            as_of=args.as_of,
            deployment_id=args.deployment_id,
            require_daily_sync=args.require_daily_sync,
            supersede=args.supersede,
        )
        live_payload: dict[str, Any] = {
            "signalId": live_result.signal_id,
            "manifest": str(live_result.manifest_path),
            "health": live_result.health.to_dict(),
        }
        if args.compare_research:
            from .live_parity import compare_research_live_scores

            manifest = json.loads(live_result.manifest_path.read_text(encoding="utf-8"))
            topk = int(manifest["canonicalConfig"]["strategy"]["topk"])
            parity = compare_research_live_scores(
                args.compare_research,
                live_result.score_path,
                signal_date=args.as_of,
                topk=topk,
                output_path=args.parity_output
                or live_result.manifest_path.parent / "research_live_parity.json",
            )
            live_payload["parity"] = parity
        print(json.dumps(live_payload, ensure_ascii=False))
        if not live_result.health.passed:
            raise SystemExit(2)
        if args.compare_research and not live_payload["parity"]["passed"]:
            raise SystemExit(3)
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

    if args.command == "backfill-extended":
        from .extended_parallel import FastExtendedDataBackfill

        result = FastExtendedDataBackfill(settings, max_workers=args.workers).backfill(
            args.start or settings.data["start_date"],
            args.end or settings.data["end_date"],
            groups=args.groups,
            force=args.force,
        )
        groups = result.get("groups", [])
        if isinstance(groups, list) and "financial" in groups:
            from .fundamentals import build_pit_from_extended

            source = settings.paths.raw / "extended" / "fina_indicator_vip"
            result["pit_fundamentals"] = (
                str(build_pit_from_extended(settings))
                if any(source.glob("trade_date=*/data.parquet"))
                else "unavailable:fina_indicator_vip"
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

    if args.command == "alpha-diagnose":
        from .research.study import run_alpha_diagnose

        manifest_path = run_alpha_diagnose(
            settings,
            acceptance=args.acceptance,
            walk_forward=args.walk_forward,
            feature_snapshot=args.feature_snapshot,
            taxonomy_path=args.taxonomy,
            output_root=args.output,
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        print(
            json.dumps(
                {
                    "studyId": manifest["studyId"],
                    "manifest": str(manifest_path),
                    "featureCount": manifest["featureCount"],
                    "rollingOosSessions": manifest["rollingOosSessions"],
                },
                ensure_ascii=False,
            )
        )
        return

    if args.command == "regime-diagnose":
        from .research.regime_study import run_regime_diagnose

        manifest_path = run_regime_diagnose(
            settings,
            base_study=args.base_study,
            acceptance=args.acceptance,
            walk_forward=args.walk_forward,
            ridge_predictions=args.ridge_predictions,
            lightgbm_predictions=args.lightgbm_predictions,
            feature_snapshot=args.feature_snapshot,
            taxonomy_path=args.taxonomy,
            regime_path=args.regimes,
            output_root=args.output,
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        print(
            json.dumps(
                {
                    "studyId": manifest["studyId"],
                    "manifest": str(manifest_path),
                    "regimeDiagnostics": manifest["status"]["regimeDiagnostics"],
                    "availability": manifest["availability"],
                },
                ensure_ascii=False,
            )
        )
        return

    if args.command == "attribution-diagnose":
        from .research.attribution_study import run_attribution_diagnose

        manifest_path = run_attribution_diagnose(
            settings,
            regime_study=args.regime_study,
            acceptance=args.acceptance,
            walk_forward=args.walk_forward,
            ridge_predictions=args.ridge_predictions,
            lightgbm_predictions=args.lightgbm_predictions,
            portfolio_runs=args.portfolio_run,
            attribution_path=args.attribution,
            output_root=args.output,
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        print(
            json.dumps(
                {
                    "studyId": manifest["studyId"],
                    "manifest": str(manifest_path),
                    "failureAttribution": manifest["status"]["failureAttribution"],
                    "primaryAlphaLossSource": manifest["primaryAlphaLossSource"],
                },
                ensure_ascii=False,
            )
        )
        return

    if args.command == "explanation-diagnose":
        from .research.explanation_study import run_explanation_diagnose

        manifest_path = run_explanation_diagnose(
            settings,
            base_study=args.base_study,
            regime_study=args.regime_study,
            attribution_study=args.attribution_study,
            acceptance=args.acceptance,
            ridge_walk_forward=args.ridge_walk_forward,
            lightgbm_walk_forward=args.lightgbm_walk_forward,
            xgboost_walk_forward=args.xgboost_walk_forward,
            feature_snapshot=args.feature_snapshot,
            taxonomy_path=args.taxonomy,
            model_artifact_roots=args.model_artifact_root,
            explanation_path=args.explanation,
            output_root=args.output,
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        print(
            json.dumps(
                {
                    "studyId": manifest["studyId"],
                    "manifest": str(manifest_path),
                    "modelExplanation": manifest["status"]["modelExplanation"],
                    "regimeConditioning": manifest["status"]["regimeConditioning"],
                    "primaryMechanism": manifest["primaryMechanism"],
                },
                ensure_ascii=False,
            )
        )
        return

    if args.command == "phase1-synthesize":
        from .research.synthesis_study import run_phase1_synthesis

        manifest_path = run_phase1_synthesis(
            settings,
            feature_study=args.feature_study,
            regime_study=args.regime_study,
            attribution_study=args.attribution_study,
            explanation_study=args.explanation_study,
            synthesis_path=args.synthesis,
            output_root=args.output,
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        print(
            json.dumps(
                {
                    "studyId": manifest["studyId"],
                    "manifest": str(manifest_path),
                    "phase1Completion": manifest["status"]["phase1Completion"],
                    "regimeDiagnostics": manifest["status"]["regimeDiagnostics"],
                    "primaryRecommendation": manifest["primaryRecommendation"],
                },
                ensure_ascii=False,
            )
        )
        return

    if args.command == "phase2-validate":
        from .research.phase2_contract import write_phase2_contract_lock

        lock_path = write_phase2_contract_lock(
            phase1_manifest=args.phase1_manifest,
            contract_path=args.contract,
            output=args.output,
        )
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        print(
            json.dumps(
                {
                    "programId": lock["programId"],
                    "primaryRecommendation": lock["recommendationRoute"]["primaryRecommendation"],
                    "allowedWorkstreams": lock["recommendationRoute"]["allowedWorkstreams"],
                    "lock": str(lock_path),
                },
                ensure_ascii=False,
            )
        )
        return

    if args.command == "phase2-plan":
        from .research.phase2_program import write_phase2_experiment_plan

        path = write_phase2_experiment_plan(
            contract_lock=args.contract_lock,
            output=args.output,
        )
        print(path)
        return

    if args.command == "phase2-data-accept":
        from .research.phase2_data_acceptance import write_data_release_v2_acceptance

        evidence = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
        checks = evidence.get("checks") if isinstance(evidence, dict) else None
        if not isinstance(checks, dict):
            raise ValueError("Phase 2 DataRelease evidence must contain a checks mapping")
        path = write_data_release_v2_acceptance(settings, evidence=checks, output=args.output)
        print(path)
        return

    if args.command == "phase2-collect":
        from .research.phase2_collector import collect_phase2_evidence

        path = collect_phase2_evidence(
            contract_lock=args.contract_lock,
            evidence_index=args.evidence,
            output=args.output,
        )
        print(path)
        return

    if args.command == "phase2-accept":
        from .research.phase2_program import write_incremental_acceptance

        path = write_incremental_acceptance(
            contract_lock=args.contract_lock,
            candidate_metrics=args.candidate_metrics,
            output=args.output,
        )
        print(path)
        return

    if args.command == "phase2-select":
        from .research.phase2_selection import write_phase2_selection_lock

        acceptance = json.loads(Path(args.acceptance).read_text(encoding="utf-8"))
        candidates = acceptance.get("candidates") if isinstance(acceptance, dict) else None
        if not isinstance(candidates, list):
            raise ValueError("Phase 2 acceptance artifact has no candidate list")
        path = write_phase2_selection_lock(
            contract_lock=args.contract_lock,
            candidates=[item for item in candidates if item.get("gatePass") is True],
            design_release_manifest=args.design_release,
            selection_date=args.selection_date,
            output=args.output,
        )
        print(path)
        return

    if args.command == "phase2-final-holdout-open":
        from .research.phase2_selection import open_final_holdout

        calendar_path = Path(args.calendar).expanduser().resolve()
        if calendar_path.suffix.lower() == ".json":
            calendar = json.loads(calendar_path.read_text(encoding="utf-8"))
        else:
            calendar_frame = pd.read_csv(calendar_path)
            if len(calendar_frame.columns) != 1:
                raise ValueError("Phase 2 holdout calendar must contain exactly one column")
            calendar = calendar_frame.iloc[:, 0].tolist()
        if not isinstance(calendar, list):
            raise ValueError("Phase 2 holdout calendar must be a JSON list or one-column CSV")
        path = open_final_holdout(
            selection_lock=args.selection_lock,
            final_release_manifest=args.final_release,
            trading_calendar=calendar,
            output=args.output,
        )
        print(path)
        return

    if args.command == "phase3-validate":
        from .research.phase3_contract import write_phase3_contract_lock

        path = write_phase3_contract_lock(
            phase2_acceptance=args.phase2_acceptance,
            phase2_evidence=args.phase2_evidence,
            phase2_data_acceptance=args.phase2_data_acceptance,
            contract_path=args.contract,
            output=args.output,
        )
        lock = json.loads(path.read_text(encoding="utf-8"))
        print(
            json.dumps(
                {
                    "programId": lock["programId"],
                    "state": lock["state"],
                    "diagnosisOnly": lock["diagnosisOnly"],
                    "lock": str(path),
                },
                ensure_ascii=False,
            )
        )
        return

    if args.command == "phase3-plan":
        from .research.phase3_program import write_phase3_experiment_plan

        path = write_phase3_experiment_plan(contract_lock=args.contract_lock, output=args.output)
        print(path)
        return

    if args.command == "phase3-diagnose":
        from .research.phase3_diagnostics import run_phase3_diagnose

        path = run_phase3_diagnose(
            settings,
            contract_lock=args.contract_lock,
            plan_path=args.plan,
            evidence_index=args.evidence,
            regime_path=args.regimes,
            output_root=args.output,
        )
        print(path)
        return

    if args.command == "phase3-portable-export":
        from .research.phase3_portability import export_phase3_portable_evidence

        path = export_phase3_portable_evidence(
            contract_lock=args.contract_lock,
            plan_path=args.plan,
            diagnosis=args.diagnosis,
            contract_path=args.contract,
            data_root=args.data_root,
            output=args.output,
        )
        print(path)
        return

    if args.command == "phase3-portable-verify":
        from .research.phase3_portability import verify_phase3_portable_evidence

        print(json.dumps(verify_phase3_portable_evidence(args.package), ensure_ascii=False, sort_keys=True))
        return
    if args.command == "backtest-predictions":
        from .prediction_backtest import backtest_predictions

        manifest_path = backtest_predictions(
            settings,
            args.predictions,
            benchmark=args.benchmark,
            topn=args.topn,
            n_drop=args.n_drop,
            hold_thresh=args.hold_thresh,
            artifact_level=args.artifact_level,
        )
        print(json.dumps(_report_payload(manifest_path), ensure_ascii=False))
        return

    if args.command in {
        "init-metadata",
        "backfill",
        "source-preflight",
        "sync-benchmark",
        "sync-universe",
    }:
        from .extract import Extractor

        if settings.uses_platform_release():
            if args.command != "source-preflight":
                raise ValueError(
                    f"{args.command} is disabled for platform_release; platform owns production ingestion"
                )
            from .platform_release import platform_release_preflight

            result = platform_release_preflight(
                settings,
                args.start or settings.data["start_date"],
                args.end or settings.data["end_date"],
            )
            print(json.dumps(result, ensure_ascii=False, default=str))
            if not result.get("passed"):
                raise SystemExit(2)
            return

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
                acceptance_mode=args.full_acceptance,
                interrupt_after_fold=args.interrupt_after_fold,
                checkpoint_namespace=args.checkpoint_namespace,
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
                print(
                    json.dumps(_report_payload(research_manifest_path, research_result), ensure_ascii=False)
                )
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
