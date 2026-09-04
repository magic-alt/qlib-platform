from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

from qlib_platform.settings import Settings
from qlib_platform.cli.parser import parser


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
    if args.command == "status":
        from qlib_platform.runtime.standalone_status import collect_status, render_status

        status_settings = Settings.load(args.config, create_dirs=False)
        payload = collect_status(status_settings)
        print(json.dumps(payload, ensure_ascii=False) if args.as_json else render_status(payload))
        return
    if args.command == "health":
        from qlib_platform.runtime.health import dependency_health, live_health, ready_health

        health_settings = Settings.load(args.config, create_dirs=False)
        payload = {
            "live": lambda: live_health(),
            "ready": lambda: ready_health(health_settings),
            "dependencies": lambda: dependency_health(health_settings),
        }[args.kind]()
        print(json.dumps(payload, ensure_ascii=False))
        return
    if args.command == "outbox":
        from qlib_platform.platform_adapter import ArtifactOutbox, OutboxWorker, PlatformClient

        outbox_settings = Settings.load(args.config, create_dirs=False)
        endpoint = str(args.endpoint or os.getenv("PLATFORM_ARTIFACT_ENDPOINT", "")).strip()
        if not endpoint:
            raise RuntimeError(
                "Platform artifact endpoint is required via --endpoint or PLATFORM_ARTIFACT_ENDPOINT"
            )
        queue = ArtifactOutbox(outbox_settings.paths.state / "platform_adapter" / "outbox.sqlite")
        client = PlatformClient(endpoint, timeout_seconds=args.timeout_seconds)
        worker = OutboxWorker(
            queue,
            client.send,
            poll_seconds=getattr(args, "poll_seconds", 30.0),
            max_poll_seconds=getattr(args, "max_poll_seconds", 300.0),
        )
        if args.outbox_command == "drain" or args.once:
            acknowledged = worker.run_once()
            print(
                json.dumps(
                    {"acknowledged": acknowledged, "pending": len(queue.pending())},
                    ensure_ascii=False,
                )
            )
            return
        worker.run_forever()
        return
    if args.command == "auth":
        import getpass
        import hmac

        from qlib_platform.auth import local_auth_backend

        auth_settings = Settings.load(args.config, create_dirs=False)
        backend = local_auth_backend(auth_settings.paths.root)
        if args.auth_command == "user-list":
            print(
                json.dumps(
                    [
                        {"username": principal.username, "roles": list(principal.roles)}
                        for principal in backend.list_users()
                    ],
                    ensure_ascii=False,
                )
            )
            return
        credential = getpass.getpass("Credential: ")
        confirmation = getpass.getpass("Confirm credential: ")
        if not hmac.compare_digest(credential, confirmation):
            raise ValueError("credential confirmation does not match")
        principal = (
            backend.bootstrap_admin(args.username, credential)
            if args.auth_command == "bootstrap-admin"
            else backend.create_user(
                args.username,
                credential,
                roles=tuple(args.role or ["researcher"]),
            )
        )
        print(json.dumps({"username": principal.username, "roles": list(principal.roles)}))
        return
    if args.command == "migrate-qlib-layout":
        from qlib_platform.datasets.layout_migration import LayoutMigrator

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
        from qlib_platform.project_audit import audit_project, write_audit as write_project_audit

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
        from qlib_platform.workflow_contract import validate_qrun_contract

        settings = Settings.load(args.config, create_dirs=False)
        result = validate_qrun_contract(settings, args.workflow)
        print(json.dumps(result, ensure_ascii=False))
        if not result["passed"]:
            raise SystemExit(2)
        return

    if args.command == "research-audit":
        from qlib_platform.backtesting.backtest_audit import (
            audit_mlflow_run,
            write_audit as write_backtest_audit,
        )

        report = audit_mlflow_run(args.run_dir)
        path = write_backtest_audit(report, args.output)
        print(json.dumps({"passed": report["passed"], "report": str(path)}, ensure_ascii=False))
        if not report["passed"]:
            raise SystemExit(2)
        return

    if args.command == "lean-register":
        from qlib_platform.ops.lean_integration import register_manifest
        from qlib_platform.releases.capabilities import (
            data_release_id_from_bundle,
            require_release_capability,
        )

        boundary_settings = Settings.load(args.config, create_dirs=False)
        release_id = data_release_id_from_bundle(args.manifest)
        require_release_capability(boundary_settings, "artifact_v2_export", reference=release_id)
        print(json.dumps(register_manifest(args.manifest, base_url=args.base_url), ensure_ascii=False))
        return

    if args.command == "artifact-v2-export":
        from qlib_platform.releases.capabilities import require_release_capability
        from qlib_platform.artifacts.research_bundle_export import (
            export_manifest_as_v2_bundle,
            resolve_data_release_id,
        )

        export_settings = Settings.load(args.config, create_dirs=False)
        source_manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        release_id = resolve_data_release_id(source_manifest, args.data_release_id)
        require_release_capability(export_settings, "artifact_v2_export", reference=release_id)

        path = export_manifest_as_v2_bundle(
            args.manifest,
            args.output_dir,
            git_commit=args.git_commit,
            container_digest=args.container_digest,
            data_release_id=release_id,
        )
        from qlib_platform.platform_adapter import ArtifactOutbox

        queued = ArtifactOutbox(export_settings.paths.state / "platform_adapter" / "outbox.sqlite").enqueue(
            path, release_id
        )
        print(
            json.dumps(
                {"manifest": str(path), "outboxItemId": queued.item_id, "outboxStatus": queued.status},
                ensure_ascii=False,
            )
        )
        return

    if args.command == "build-target-portfolio":
        from qlib_platform.releases.capabilities import (
            data_release_id_from_artifact,
            require_release_capability,
        )
        from qlib_platform.backtesting.trade_plan import build_trade_plan, resolve_selection_path

        boundary_settings = Settings.load(args.config, create_dirs=False)
        selection_path = resolve_selection_path(
            args.portfolio_config,
            selection_file=args.selection_file,
            selection_date=args.selection_date,
        )
        release_id = data_release_id_from_artifact(selection_path)
        require_release_capability(boundary_settings, "target_portfolio", reference=release_id)

        path, plan = build_trade_plan(
            config_path=args.portfolio_config,
            selection_file=selection_path,
            selection_date=args.selection_date,
            prev_selection_file=args.current_portfolio,
            trade_date=args.trade_date,
        )
        print(json.dumps({"file": str(path), "rows": len(plan)}, ensure_ascii=False))
        return

    if args.command == "research-gate":
        import yaml
        from qlib_platform.research.evaluation.gates import (
            ResearchThresholds,
            evaluate_research_metrics,
            write_gate_report,
        )

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
        from qlib_platform.ops.lean_bridge import export_lean_targets
        from qlib_platform.releases.capabilities import (
            data_release_id_from_artifact,
            require_release_capability,
        )

        boundary_settings = Settings.load(args.config, create_dirs=False)
        release_id = data_release_id_from_artifact(args.target_file)
        require_release_capability(boundary_settings, "target_portfolio", reference=release_id)
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
        from qlib_platform.data.fundamentals import ingest_pit_fundamentals

        pit_settings = Settings.load(args.config, create_dirs=False)
        calendar = args.calendar or str(pit_settings.paths.metadata / "trade_calendar.parquet")
        output = args.output or str(pit_settings.paths.curated / "fundamentals_pit.parquet")
        print(ingest_pit_fundamentals(args.reports, calendar, output))
        return

    # Qlib export resolves the checkout that actually supplies the imported
    # package, so a stale optional QLIB_REPO does not mask a valid editable
    # installation before export can validate it.
    settings = Settings.load(args.config, require_tushare=False)
    if args.command == "sync-industry":
        from qlib_platform.data.industry import sync_sw2021_industry

        path = sync_sw2021_industry(settings, coverage_end=args.end)
        print(json.dumps({"industryClassificationPit": str(path)}, ensure_ascii=False))
        return
    if args.command == "bootstrap":
        from qlib_platform.bootstrap import bootstrap

        result = bootstrap(
            settings,
            source=args.source,
            path=args.path,
            start=args.start,
            end=args.end,
        )
        print(json.dumps(result, ensure_ascii=False, default=str))
        return
    if args.command == "migration-acceptance":
        from qlib_platform.datasets.migration_acceptance import run_migration_acceptance

        evidence = run_migration_acceptance(
            settings,
            source_kind=args.source,
            source_root=args.source_root,
            acceptance_root=args.acceptance_root,
            start=args.start,
            end=args.end,
            single_thread=args.single_thread,
        )
        print(json.dumps({"evidence": str(evidence)}, ensure_ascii=False))
        return
    if args.command == "release":
        from qlib_platform.datasets.dataset_registry import DatasetRegistry
        from qlib_platform.releases import FileReleaseStore, import_qlib_dataset, release_store_root

        store = FileReleaseStore(release_store_root(settings))
        if args.release_command == "list":
            print(
                json.dumps(
                    [
                        {
                            "dataReleaseId": item.data_release_id,
                            "profile": item.profile,
                            "manifestSha256": item.manifest_sha256,
                            "manifest": str(item.manifest_path),
                        }
                        for item in store.list()
                    ],
                    ensure_ascii=False,
                )
            )
        elif args.release_command == "verify":
            verification: dict[str, object] = {}
            release_value = store.resolve(
                args.reference,
                mode=args.mode,
                receipt_dir=settings.paths.state / "verification_receipts",
                reuse_receipt=args.reuse_receipt,
                sample_size=args.sample_size,
                evidence=verification,
                workers=args.workers,
            )
            print(
                json.dumps(
                    {
                        "verified": True,
                        "dataReleaseId": release_value.data_release_id,
                        "manifestSha256": release_value.manifest_sha256,
                        "verification": verification,
                    }
                )
            )
        elif args.release_command == "import-qlib":
            release_value, dataset = import_qlib_dataset(settings, args.path)
            print(
                json.dumps(
                    {
                        "dataReleaseId": release_value.data_release_id,
                        "datasetVersionId": dataset.version_id,
                        "governanceLevel": "exploratory",
                    }
                )
            )
        elif args.release_command in {"build-local", "build-tushare"}:
            from qlib_platform.bootstrap import bootstrap

            source = "raw" if args.release_command == "build-local" else "tushare"
            print(
                json.dumps(
                    bootstrap(settings, source=source, start=args.start, end=args.end),
                    ensure_ascii=False,
                )
            )
        else:
            release_value = store.resolve(args.reference)
            registry = DatasetRegistry(settings.registry_path)
            registry.register_release(release_value)
            registry.promote_release(args.alias, release_value.data_release_id)
            print(json.dumps({"alias": args.alias, "dataReleaseId": release_value.data_release_id}))
        return
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
            from qlib_platform.research.hypotheses.catalog import bind_candidate_hypothesis

            binding = bind_candidate_hypothesis(
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
        from qlib_platform.datasets.dataset_manifest import verify_dataset_manifest
        from qlib_platform.datasets.dataset_registry import DatasetRegistry
        from qlib_platform.datasets.dataset_resolver import resolve_dataset

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
                verification = {}
                mode = "manifest" if args.metadata_only else args.mode
                if args.metadata_only and args.mode == "sampled":
                    raise ValueError("--metadata-only cannot be combined with an explicit --mode")
                verified = verify_dataset_manifest(
                    resolved.manifest_path,
                    mode=mode,
                    receipt_dir=settings.paths.state / "verification_receipts",
                    reuse_receipt=args.reuse_receipt,
                    sample_size=args.sample_size,
                    evidence=verification,
                    workers=args.workers,
                )
                print(
                    json.dumps(
                        {
                            "versionId": verified["version_id"],
                            "verified": True,
                            "verification": verification,
                        }
                    )
                )
        return

    dataset_ref = getattr(args, "dataset_ref", None)
    if dataset_ref:
        from dataclasses import replace
        from qlib_platform.datasets.dataset_resolver import resolve_dataset

        resolved = resolve_dataset(settings, dataset_ref, allow_legacy=False)
        settings = replace(settings, qlib_data_uri=resolved.data_path)

    if args.command == "dataset-build":
        import uuid

        from qlib_platform.datasets.dataset_registry import DatasetRegistry
        from qlib_platform.data.fundamentals import build_pit_from_extended
        from qlib_platform.datasets.lakehouse import freeze_pipeline_layers
        from qlib_platform.data.normalize import build_all_curated, export_full_staging
        from qlib_platform.datasets.qlib_export import dump_full

        run_id = f"dataset-build-{uuid.uuid4().hex}"
        run_registry = DatasetRegistry(settings.registry_path)
        run_registry.start_pipeline_run(run_id, "dataset_build")
        try:
            if settings.uses_platform_release():
                from qlib_platform.ops.platform_release import materialize_platform_release

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
                from qlib_platform.releases import publish_local_research_release

                release = publish_local_research_release(
                    settings,
                    start=args.start or settings.data["start_date"],
                    end=args.end or settings.data["end_date"],
                )
                path = dump_full(
                    settings,
                    single_thread=args.single_thread,
                    sync_context={
                        "data_release_id": release.data_release_id,
                        "data_release_manifest_sha256": release.manifest_sha256,
                        "dataset_parents": [
                            {"version_id": snapshots[-1]["version_id"], "relation": "converted_from"}
                        ],
                    },
                    promote_alias=False,
                )
                dataset_payload = json.loads((path / "dataset_manifest.json").read_text(encoding="utf-8"))
                run_registry.register_release(release, governance_level="research")
                run_registry.promote_research_snapshot(
                    release_alias="research-release-current",
                    data_release_id=release.data_release_id,
                    dataset_alias=settings.qlib_dataset_ref,
                    dataset_version_id=str(dataset_payload["version_id"]),
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
        from qlib_platform.ops.ops_cli import query_ops

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
    if args.command == "feedback-build-labels":
        from qlib_platform.feedback.realized_labels import RealizedLabelSpec, write_realized_label_snapshot

        labels = pd.read_parquet(Path(args.labels).expanduser().resolve())
        if {"datetime", "instrument"}.issubset(labels.columns):
            labels = labels.set_index(["datetime", "instrument"])
        calendar = Path(args.calendar).expanduser().resolve().read_text(encoding="utf-8").splitlines()
        manifest = write_realized_label_snapshot(
            args.output,
            labels,
            spec=RealizedLabelSpec(
                data_release_id=args.data_release_id,
                label_spec_id=args.label_spec_id,
                horizon_days=args.horizon_days,
                signal_lag_days=args.signal_lag_days,
                price_field=args.price_field,
                source_artifact_id=args.source_artifact_id,
            ),
            trading_calendar=calendar,
            observed_through=args.observed_through,
        )
        print(json.dumps(manifest, ensure_ascii=False))
        return
    if args.command == "feedback-evaluate":
        from qlib_platform.feedback.prediction_evaluation import evaluate_prediction_snapshot

        manifest = evaluate_prediction_snapshot(
            args.output,
            prediction_snapshot=args.predictions,
            realized_label_snapshot=args.realized_labels,
            topk=args.topk,
            min_cross_section=args.min_cross_section,
            rolling_window=args.rolling_window,
        )
        print(json.dumps(manifest, ensure_ascii=False))
        if manifest["decision"]["status"] != "PASS":
            raise SystemExit(2)
        return
    if args.command == "ops-retry-delivery":
        from qlib_platform.ops.ops_cli import state_from_settings

        state_from_settings(settings).recover_delivery(args.idempotency_key)
        print(json.dumps({"idempotencyKey": args.idempotency_key, "status": "RETRY_READY"}))
        return
    if args.command == "ops-ack":
        from qlib_platform.ops.ops_cli import state_from_settings

        state_from_settings(settings).acknowledge(
            args.entity, args.entity_id, operator=args.operator, reason=args.reason
        )
        print(json.dumps({"entity": args.entity, "id": args.entity_id, "acknowledged": True}))
        return
    if args.command == "ops-summary":
        from qlib_platform.ops.ops_cli import export_daily_ops, state_from_settings

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
        from qlib_platform.models.production_refit import refit_production_model

        path = refit_production_model(settings, args.research_run, as_of=args.as_of)
        print(json.dumps({"manifest": str(path)}, ensure_ascii=False))
        return
    if args.command in {"model-deploy", "model-rollback", "model-status"}:
        from qlib_platform.models.model_registry import ModelRegistry

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
        from qlib_platform.runtime.live_inference import run_live_inference

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
            from qlib_platform.runtime.live_parity import compare_research_live_scores

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
        from qlib_platform.runtime.daily_signal_runner import run_daily_signal

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
        from qlib_platform.data.daily_sync import run_daily_sync

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
        from qlib_platform.data.corporate_actions import CorporateActionStore
        from qlib_platform.data.ingestion import Extractor

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
        from qlib_platform.data.extended_parallel import FastExtendedDataBackfill

        result = FastExtendedDataBackfill(settings, max_workers=args.workers).backfill(
            args.start or settings.data["start_date"],
            args.end or settings.data["end_date"],
            groups=args.groups,
            force=args.force,
        )
        groups = result.get("groups", [])
        if isinstance(groups, list) and "financial" in groups:
            from qlib_platform.data.fundamentals import build_pit_from_extended

            pit_source = settings.paths.raw / "extended" / "fina_indicator_vip"
            result["pit_fundamentals"] = (
                str(build_pit_from_extended(settings))
                if any(pit_source.glob("trade_date=*/data.parquet"))
                else "unavailable:fina_indicator_vip"
            )
        print(json.dumps(result, ensure_ascii=False))
        return
    if args.command == "export-kline":
        from qlib_platform.data.kline_export import export_kline

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
        from qlib_platform.backtesting.backtest_report import write_backtest_report

        run_dir = Path(args.run_dir).expanduser().resolve()
        write_backtest_report(settings, run_dir, positions_file=args.positions_file)
        print(json.dumps(_report_payload(run_dir / "manifest.json"), ensure_ascii=False))
        return

    if args.command == "alpha-diagnose":
        from qlib_platform.research.studies.alpha import run_alpha_diagnose

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
        from qlib_platform.research.studies.regime import run_regime_diagnose

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
        from qlib_platform.research.studies.attribution import run_attribution_diagnose

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
        from qlib_platform.research.studies.explanation import run_explanation_diagnose

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

    if args.command == "research-synthesize":
        from qlib_platform.research.studies.synthesis import run_research_synthesis

        manifest_path = run_research_synthesis(
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

    candidate_commands = {
        "candidate-validate",
        "candidate-plan",
        "candidate-data-accept",
        "candidate-collect",
        "candidate-accept",
        "candidate-select",
        "final-holdout-open",
    }
    stability_commands = {
        "stability-validate",
        "stability-plan",
        "stability-diagnose",
        "stability-portable-export",
    }
    if args.command in candidate_commands or args.command in stability_commands:
        from qlib_platform.releases.capabilities import require_release_capability

        # Persisted capability identifiers are governance identities, not module boundaries.
        require_release_capability(
            settings,
            "phase2" if args.command in candidate_commands else "phase3",
        )

    if args.command == "candidate-validate":
        from qlib_platform.research.contracts.candidate_program import write_candidate_contract_lock

        lock_path = write_candidate_contract_lock(
            phase1_manifest=args.synthesis_manifest,
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

    if args.command == "candidate-plan":
        from qlib_platform.research.workflow.candidate_program import write_candidate_experiment_plan

        path = write_candidate_experiment_plan(
            contract_lock=args.contract_lock,
            output=args.output,
        )
        print(path)
        return

    if args.command == "candidate-data-accept":
        from qlib_platform.research.evidence.data_acceptance import write_data_release_v2_acceptance

        evidence = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
        checks = evidence.get("checks") if isinstance(evidence, dict) else None
        if not isinstance(checks, dict):
            raise ValueError("Phase 2 DataRelease evidence must contain a checks mapping")
        path = write_data_release_v2_acceptance(settings, evidence=checks, output=args.output)
        print(path)
        return

    if args.command == "candidate-collect":
        from qlib_platform.research.evidence.collector import collect_candidate_evidence

        path = collect_candidate_evidence(
            contract_lock=args.contract_lock,
            evidence_index=args.evidence,
            output=args.output,
        )
        print(path)
        return

    if args.command == "candidate-accept":
        from qlib_platform.research.workflow.candidate_program import write_incremental_acceptance

        path = write_incremental_acceptance(
            contract_lock=args.contract_lock,
            candidate_metrics=args.candidate_metrics,
            output=args.output,
        )
        print(path)
        return

    if args.command == "candidate-select":
        from qlib_platform.research.evaluation.selection import write_candidate_selection_lock

        acceptance = json.loads(Path(args.acceptance).read_text(encoding="utf-8"))
        candidates = acceptance.get("candidates") if isinstance(acceptance, dict) else None
        if not isinstance(candidates, list):
            raise ValueError("Phase 2 acceptance artifact has no candidate list")
        path = write_candidate_selection_lock(
            contract_lock=args.contract_lock,
            candidates=[item for item in candidates if item.get("gatePass") is True],
            design_release_manifest=args.design_release,
            selection_date=args.selection_date,
            output=args.output,
        )
        print(path)
        return

    if args.command == "final-holdout-open":
        from qlib_platform.research.evaluation.selection import open_final_holdout

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

    if args.command == "stability-validate":
        from qlib_platform.research.contracts.stability_program import write_stability_contract_lock

        path = write_stability_contract_lock(
            candidate_acceptance=args.candidate_acceptance,
            phase2_evidence=args.candidate_evidence,
            candidate_data_acceptance=args.candidate_data_acceptance,
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

    if args.command == "stability-plan":
        from qlib_platform.research.workflow.stability_program import write_stability_experiment_plan

        path = write_stability_experiment_plan(contract_lock=args.contract_lock, output=args.output)
        print(path)
        return

    if args.command == "stability-diagnose":
        from qlib_platform.research.diagnostics.stability import run_stability_diagnostics

        path = run_stability_diagnostics(
            settings,
            contract_lock=args.contract_lock,
            plan_path=args.plan,
            evidence_index=args.evidence,
            regime_path=args.regimes,
            output_root=args.output,
        )
        print(path)
        return

    if args.command == "stability-portable-export":
        from qlib_platform.research.diagnostics.portability import export_stability_portable_evidence

        path = export_stability_portable_evidence(
            contract_lock=args.contract_lock,
            plan_path=args.plan,
            diagnosis=args.diagnosis,
            contract_path=args.contract,
            data_root=args.data_root,
            output=args.output,
        )
        print(path)
        return

    if args.command == "stability-portable-verify":
        from qlib_platform.research.diagnostics.portability import verify_stability_portable_evidence

        print(json.dumps(verify_stability_portable_evidence(args.package), ensure_ascii=False, sort_keys=True))
        return
    if args.command == "backtest-predictions":
        from qlib_platform.backtesting.prediction_backtest import backtest_predictions

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
        from qlib_platform.data.ingestion import Extractor

        if settings.uses_platform_release():
            if args.command != "source-preflight":
                raise ValueError(
                    f"{args.command} is disabled for platform_release; platform owns production ingestion"
                )
            from qlib_platform.ops.platform_release import platform_release_preflight

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
        from qlib_platform.data.normalize import (
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
        from qlib_platform.datasets.qlib_export import dump_full, dump_update

        path = (
            dump_full(settings, single_thread=args.single_thread)
            if args.command == "dump-full"
            else dump_update(settings, single_thread=args.single_thread)
        )
        print(path)
    elif args.command == "runtime-probe":
        from qlib_platform.models.model_runtime import load_model_profile, resolve_runtime

        runtime = resolve_runtime(load_model_profile(settings, args.model_profile))
        print(json.dumps(runtime.to_manifest(), ensure_ascii=False))
    elif args.command == "feature-store":
        from qlib_platform.research.features.store import prepare_feature_data

        _, feature_metadata = prepare_feature_data(
            settings,
            args.start or settings.data["start_date"],
            args.end or settings.data["end_date"],
            force=args.force,
        )
        print(json.dumps(feature_metadata, ensure_ascii=False))
    elif args.command in {"train-select", "research-run"}:
        from qlib_platform.research.workflow.train_select import train_backtest_select

        if args.command == "research-run" and args.mode == "walk-forward":
            if args.stage != "release":
                raise ValueError("walk-forward currently requires --stage release")
            from qlib_platform.research.workflow.walk_forward import run_walk_forward

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
