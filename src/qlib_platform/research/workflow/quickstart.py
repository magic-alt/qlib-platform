from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml

from qlib_platform.alpha.registry import ALPHA_PACKS, assert_alpha_pack_compatible
from qlib_platform.bootstrap import bootstrap
from qlib_platform.datasets.data_source_resolver import ReleaseSelectionRequired, resolve_source
from qlib_platform.datasets.dataset_manifest import verify_dataset_manifest
from qlib_platform.datasets.dataset_resolver import resolve_dataset
from qlib_platform.models.model_runtime import load_model_profile, resolve_runtime
from qlib_platform.research.interfaces.cli_ux import (
    filter_known_child_noise,
    render_terminal_summary,
    summarize_result,
)
from qlib_platform.runtime.runtime_resources import resource_argument, resource_path
from qlib_platform.settings import Settings

MODEL_PRESETS = {
    "ridge": "configs/model_profiles/ridge_golden_v1.yaml",
    "lightgbm": "configs/model_profiles/lightgbm_auto.yaml",
    "xgboost": "configs/model_profiles/xgboost_cpu_v1.yaml",
    "pytorch": "configs/model_profiles/pytorch_auto.yaml",
}
MATRIX_ALPHA_PACKS = ("alpha158_market_v1", "alpha158_daily_v1", "alpha158_pit_v1")
MATRIX_MODELS = ("ridge", "lightgbm", "xgboost")


def _verification_args(p: argparse.ArgumentParser, *, default_mode: str = "deep") -> None:
    p.add_argument("--verify-mode", choices=["manifest", "sampled", "deep"], default=default_mode)
    p.add_argument("--sample-size", type=int, default=64)
    p.add_argument("--workers", type=int, default=4)


def _research_args(p: argparse.ArgumentParser, *, matrix: bool = False) -> None:
    p.add_argument("--dataset-ref")
    p.add_argument("--alpha-pack", action="append", choices=sorted(ALPHA_PACKS))
    p.add_argument("--model", action="append", choices=sorted(MODEL_PRESETS))
    p.add_argument("--model-profile", action="append", default=[], metavar="PATH")
    p.add_argument("--mode", choices=["fixed", "walk-forward"], default="fixed")
    p.add_argument("--train", nargs=2, metavar=("START", "END"))
    p.add_argument("--valid", nargs=2, metavar=("START", "END"))
    p.add_argument("--test", nargs=2, metavar=("START", "END"))
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--benchmark", default="SH000300")
    p.add_argument("--topn", type=int)
    p.add_argument("--artifact-level", choices=["minimal", "full"], default="full")
    p.add_argument(
        "--stage",
        choices=["signal", "release"],
        default="signal",
        help="fixed-mode stage; signal is the exploratory default",
    )
    p.add_argument(
        "--prediction-backtest",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="backtest fixed signal predictions without promoting the model",
    )
    p.add_argument(
        "--verbose-child-output",
        action="store_true",
        help="show unfiltered child stderr and runtime-probe output",
    )
    p.add_argument("--output")
    p.add_argument("--continue-on-error", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    _verification_args(p)
    if matrix:
        p.set_defaults(matrix_defaults=True)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Local-data research quickstart for qlib-platform")
    p.add_argument("--config", default=resource_argument("configs/pipeline.standalone.yaml"))
    sub = p.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="inspect data, DatasetVersion, AlphaPacks and runtimes")
    doctor.add_argument("--dataset-ref")
    _verification_args(doctor, default_mode="sampled")

    prepare = sub.add_parser("prepare", help="auto import/build/bootstrap local research data")
    prepare.add_argument("--source", choices=["auto", "qlib", "raw", "tushare"], default="auto")
    prepare.add_argument("--path")
    prepare.add_argument("--start")
    prepare.add_argument("--end")
    prepare.add_argument("--dataset-ref")
    _verification_args(prepare, default_mode="sampled")

    catalog = sub.add_parser("catalog", help="list AlphaPacks and model presets")
    catalog.add_argument("--json", action="store_true", dest="as_json")

    plan = sub.add_parser("plan", help="write exact commands/config overlays without training")
    _research_args(plan)
    run = sub.add_parser("run", help="run selected AlphaPack/model experiments")
    _research_args(run)
    matrix = sub.add_parser("matrix", help="run Alpha158 Market/Daily/PIT x Ridge/LGB/XGB")
    _research_args(matrix, matrix=True)

    backtest = sub.add_parser("backtest", help="prediction-only portfolio backtest")
    backtest.add_argument("predictions")
    backtest.add_argument("--dataset-ref")
    backtest.add_argument("--benchmark", default="SH000300")
    backtest.add_argument("--topn", type=int)
    backtest.add_argument("--n-drop", type=int)
    backtest.add_argument("--hold-thresh", type=int)
    backtest.add_argument("--artifact-level", choices=["minimal", "full"], default="minimal")
    return p


def _dataset_ref(settings: Settings, requested: str | None) -> str:
    return str(requested or settings.qlib_dataset_ref)


def _release_selection_payload(settings: Settings, error: Exception) -> dict[str, Any]:
    return {
        "status": "RELEASE_SELECTION_REQUIRED",
        "error": str(error),
        "recommendedCommand": "tq release list",
        "selectionCommand": "tq release promote <DATA_RELEASE_ID> --alias research-release-current",
        "datasetRecoveryCommand": f"tq registry-rebuild --root {settings.paths.root}",
        "retryCommand": "tq-research prepare --source auto",
    }


def _verify(settings: Settings, reference: str, args: argparse.Namespace) -> dict[str, Any]:
    resolved = resolve_dataset(settings, reference, allow_legacy=False)
    evidence: dict[str, object] = {}
    manifest = verify_dataset_manifest(
        resolved.manifest_path,
        mode=args.verify_mode,
        sample_size=args.sample_size,
        workers=args.workers,
        receipt_dir=settings.paths.state / "verification_receipts",
        reuse_receipt=True,
        evidence=evidence,
    )
    semantic = manifest.get("semantic_contract", {})
    semantic = semantic if isinstance(semantic, Mapping) else {}
    return {
        "reference": reference,
        "versionId": resolved.version_id,
        "path": str(resolved.data_path),
        "dataReleaseId": manifest.get("data_release_id") or semantic.get("data_release_id"),
        "verification": evidence,
    }


def _models(settings: Settings) -> list[dict[str, Any]]:
    result = []
    for name, relative in MODEL_PRESETS.items():
        path = resource_path(relative).expanduser().resolve()
        row: dict[str, Any] = {"name": name, "profile": str(path), "available": False}
        try:
            profile = load_model_profile(settings, path)
            runtime = resolve_runtime(profile)
            row.update(
                available=True,
                family=profile.family,
                requestedDevice=profile.device,
                resolvedDevice=runtime.resolved_device,
                fallbackReason=runtime.fallback_reason,
            )
        except Exception as exc:
            row["error"] = str(exc)
        result.append(row)
    return result


def _alphas(settings: Settings, dataset: Path | None = None) -> list[dict[str, Any]]:
    bound = replace(settings, qlib_data_uri=dataset) if dataset else settings
    result = []
    for pack_id, pack in ALPHA_PACKS.items():
        row: dict[str, Any] = {
            "id": pack_id,
            "handler": pack.handler_class,
            "warmupTradingDays": pack.warmup_trading_days,
            "requiredReleaseComponents": list(pack.required_release_components),
            "compatible": None,
        }
        if dataset:
            try:
                assert_alpha_pack_compatible(bound, pack)
                row["compatible"] = True
            except Exception as exc:
                row.update(compatible=False, error=str(exc))
        result.append(row)
    return result


def doctor(settings: Settings, args: argparse.Namespace) -> dict[str, Any]:
    reference = _dataset_ref(settings, args.dataset_ref)
    try:
        source = resolve_source(settings)
    except ReleaseSelectionRequired as exc:
        return _release_selection_payload(settings, exc)
    visible_reference = (
        source.reference
        if source.status == "READY" or settings.mode != "standalone"
        else settings.qlib_dataset_ref
    )
    payload: dict[str, Any] = {
        "status": source.status,
        "source": source.source,
        "reference": visible_reference,
        "action": source.action,
        "profile": source.profile,
        "missingComponents": list(source.missing_components),
    }
    if source.status != "READY":
        payload["recommendedCommand"] = (
            "tq-research run --alpha-pack alpha158_market_v1 --model lightgbm"
            if settings.mode == "standalone"
            else "tq-research prepare --source auto"
        )
        return payload
    dataset = _verify(settings, reference, args)
    payload.update(
        status="READY",
        dataset=dataset,
        alphaPacks=_alphas(settings, Path(str(dataset["path"]))),
        models=_models(settings),
        recommendedCommand=(
            f"tq-research run --dataset-ref {reference} --alpha-pack alpha158_market_v1 --model lightgbm"
        ),
    )
    return payload


def prepare(settings: Settings, args: argparse.Namespace) -> dict[str, Any]:
    result = bootstrap(
        settings,
        source=args.source,
        path=args.path,
        start=args.start,
        end=args.end,
    )
    if str(result.get("status")) != "READY":
        payload: dict[str, Any] = {"status": result.get("status", "NOT_READY"), "bootstrap": result}
        for key in (
            "error",
            "recommendedCommand",
            "selectionCommand",
            "datasetRecoveryCommand",
            "retryCommand",
        ):
            if key in result:
                payload[key] = result[key]
        return payload
    return {
        "status": "READY",
        "bootstrap": result,
        "dataset": _verify(settings, _dataset_ref(settings, args.dataset_ref), args),
    }


def catalog(settings: Settings) -> dict[str, Any]:
    return {
        "alphaPacks": _alphas(settings),
        "modelPresets": [
            {"name": name, "profile": str(resource_path(path).expanduser().resolve())}
            for name, path in MODEL_PRESETS.items()
        ],
        "defaultMatrix": {"alphaPacks": list(MATRIX_ALPHA_PACKS), "models": list(MATRIX_MODELS)},
    }


def _selected(args: argparse.Namespace) -> tuple[tuple[str, ...], tuple[tuple[str, Path], ...]]:
    matrix = bool(getattr(args, "matrix_defaults", False))
    alphas = tuple(args.alpha_pack or (MATRIX_ALPHA_PACKS if matrix else ("alpha158_market_v1",)))
    names = tuple(args.model or (MATRIX_MODELS if matrix else ("lightgbm",)))
    profiles = [(name, resource_path(MODEL_PRESETS[name]).expanduser().resolve()) for name in names]
    profiles.extend((Path(raw).stem, Path(raw).expanduser().resolve()) for raw in args.model_profile)
    unique = []
    seen = set()
    for name, path in profiles:
        if str(path) not in seen:
            unique.append((name, path))
            seen.add(str(path))
    return alphas, tuple(unique)


def _overlay(settings: Settings, root: Path, alpha: str) -> Path:
    path = root / "configs" / f"{alpha}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "extends": str(settings.config_path.resolve()),
                # Generated overlays live under data/output/quickstart.  Inherited relative
                # paths would otherwise be re-based against that nested file location when
                # the child CLI reloads the overlay.  Pin the already-resolved data anchors
                # so the child process sees exactly the same DatasetVersion and registry.
                "project_root": str(settings.paths.root.resolve()),
                "storage": {"registry_path": str(settings.registry_path)},
                "qlib": {
                    "dataset_dir": str(settings.qlib_data_uri),
                    "versions_root": str(settings.qlib_versions_root),
                },
                "experiment": {"alpha": {"pack": alpha}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def _base(config: Path) -> list[str]:
    return [sys.executable, "-m", "qlib_platform", "--config", str(config)]


def build_research_command(
    *,
    config: Path,
    mode: str,
    dataset_ref: str,
    model_profile: Path,
    benchmark: str,
    topn: int | None,
    artifact_level: str,
    train: tuple[str, str] | list[str] | None,
    valid: tuple[str, str] | list[str] | None,
    test: tuple[str, str] | list[str] | None,
    start: str | None,
    end: str | None,
    checkpoint_namespace: str,
    stage: str = "signal",
) -> list[str]:
    if mode == "fixed":
        if any(x is not None for x in (train, valid, test)) and not all(
            x is not None for x in (train, valid, test)
        ):
            raise ValueError("fixed research requires --train, --valid and --test together")
        command = [
            *_base(config),
            "train-select",
            "--dataset-ref",
            dataset_ref,
            "--model-profile",
            str(model_profile),
            "--benchmark",
            benchmark,
            "--stage",
            stage,
            "--artifact-level",
            artifact_level,
        ]
        for flag, value in (("--train", train), ("--valid", valid), ("--test", test)):
            if value:
                command.extend([flag, *value])
    else:
        if any(x is not None for x in (train, valid, test)):
            raise ValueError("walk-forward uses --start/--end, not fixed split options")
        command = [
            *_base(config),
            "research-run",
            "--mode",
            "walk-forward",
            "--dataset-ref",
            dataset_ref,
            "--model-profile",
            str(model_profile),
            "--benchmark",
            benchmark,
            "--stage",
            "release",
            "--artifact-level",
            artifact_level,
            "--checkpoint-namespace",
            checkpoint_namespace,
        ]
        if start:
            command.extend(["--start", start])
        if end:
            command.extend(["--end", end])
    if topn is not None:
        command.extend(["--topn", str(topn)])
    return command


def _last_json(text: str) -> dict[str, Any] | None:
    for line in reversed([x.strip() for x in text.splitlines() if x.strip()]):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _execute(
    command: list[str], *, verbose: bool = False, echo_stdout: bool = True
) -> tuple[int, dict[str, Any] | None]:
    run = subprocess.run(command, text=True, capture_output=True, check=False)
    if echo_stdout and run.stdout:
        print(run.stdout, end="")
    stderr = run.stderr if verbose else filter_known_child_noise(run.stderr)
    if stderr:
        print(stderr, end="", file=sys.stderr)
    return run.returncode, _last_json(run.stdout)


def _attach_summary(
    settings: Settings, job: dict[str, Any], result: dict[str, Any] | None
) -> dict[str, Any] | None:
    summary = summarize_result(settings.paths.output, result)
    if summary:
        job["summary"] = summary
        if result is not None and summary.get("manifest"):
            result.setdefault("manifest", str(summary["manifest"]))
    return result


def _predictions(result: Mapping[str, Any] | None) -> Path | None:
    if not result or not result.get("manifest"):
        return None
    path = Path(str(result["manifest"])).expanduser()
    if not path.is_file():
        return None
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for item in manifest.get("artifacts", []):
        if isinstance(item, Mapping) and item.get("name") == "oos_predictions.parquet":
            return Path(str(item["localPath"])).expanduser()
    return None


def _matrix_markdown(payload: Mapping[str, Any]) -> str:
    lines = [
        "# Local research matrix",
        "",
        f"Dataset: `{payload['datasetRef']}`  ",
        f"Mode: `{payload['mode']}`",
        "",
        "| AlphaPack | Model | Status | Research manifest | Portfolio manifest |",
        "| --- | --- | --- | --- | --- |",
    ]
    for job in payload["jobs"]:
        result = job.get("result") or {}
        backtest = job.get("predictionBacktest") or {}
        backtest_result = backtest.get("result") or {}
        lines.append(
            f"| {job['alphaPack']} | {job['model']} | {job.get('status', 'PLANNED')} | "
            f"{result.get('manifest', '-')} | {backtest_result.get('manifest', '-')} |"
        )
    lines.extend(
        [
            "",
            "> Research evidence only. Current governance state still controls candidates, holdout and "
            "publishing.",
            "",
        ]
    )
    return "\n".join(lines)


def _write_matrix(root: Path, payload: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "research_matrix.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (root / "research_matrix.md").write_text(_matrix_markdown(payload), encoding="utf-8")


def build_plan(settings: Settings, args: argparse.Namespace, root: Path) -> dict[str, Any]:
    reference = _dataset_ref(settings, args.dataset_ref)
    alphas, profiles = _selected(args)
    jobs = []
    for alpha in alphas:
        config = _overlay(settings, root, alpha)
        for model, profile in profiles:
            namespace = f"quickstart-{alpha}-{model}".replace("_", "-")
            command = build_research_command(
                config=config,
                mode=args.mode,
                dataset_ref=reference,
                model_profile=profile,
                benchmark=args.benchmark,
                topn=args.topn,
                artifact_level=args.artifact_level,
                train=args.train,
                valid=args.valid,
                test=args.test,
                start=args.start,
                end=args.end,
                checkpoint_namespace=namespace,
                stage=args.stage,
            )
            jobs.append(
                {
                    "alphaPack": alpha,
                    "model": model,
                    "modelProfile": str(profile),
                    "config": str(config),
                    "command": command,
                }
            )
    return {
        "schemaVersion": "1.0",
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "datasetRef": reference,
        "mode": args.mode,
        "stage": "release" if args.mode == "walk-forward" else args.stage,
        "predictionBacktest": bool(
            args.mode == "fixed" and args.stage == "signal" and args.prediction_backtest
        ),
        "jobs": jobs,
        "governanceNote": "Convenience workflow only; it does not override docs/current_state.md.",
    }


def _preparation_failure(
    settings: Settings,
    plan: dict[str, Any],
    root: Path,
    result: Mapping[str, Any],
    error: Exception,
) -> int:
    status = str(result.get("status") or "DATASET_PREPARATION_REQUIRED")
    plan.update(status=status, error=str(result.get("error") or error), failureCount=1)
    if status == "DATA_UNAVAILABLE":
        plan["recommendedCommand"] = (
            "Set TUSHARE_TOKEN in .env to download data, or place existing local data under QLIB_DATA_ROOT"
        )
    else:
        plan["recommendedCommand"] = str(
            result.get("recommendedCommand") or "tq-research prepare --source auto"
        )
    # Explicit release/version selection remains available in integrated mode, but
    # standalone quickstart never makes users handle content-addressed IDs.
    if settings.mode != "standalone":
        for key in (
            "selectionCommand",
            "datasetRecoveryCommand",
            "retryCommand",
            "reference",
            "action",
        ):
            if key in result:
                plan[key] = result[key]
    _write_matrix(root, plan)
    return 2


def run_plan(settings: Settings, args: argparse.Namespace, plan: dict[str, Any], root: Path) -> int:
    try:
        dataset = _verify(settings, str(plan["datasetRef"]), args)
    except KeyError as exc:
        if str(plan["datasetRef"]) != settings.qlib_dataset_ref:
            raise
        if args.dry_run:
            plan.update(
                status="DATASET_PREPARATION_REQUIRED",
                error=str(exc),
                recommendedCommand="tq-research prepare --source auto",
                failureCount=1,
            )
            _write_matrix(root, plan)
            return 2
        try:
            prepared = bootstrap(settings, source="auto")
        except Exception as prep_error:
            plan.update(
                status="DATASET_PREPARATION_FAILED",
                error=f"{type(prep_error).__name__}: {prep_error}",
                recommendedCommand="tq-research doctor",
                failureCount=1,
            )
            _write_matrix(root, plan)
            return 2
        if str(prepared.get("status")) != "READY":
            return _preparation_failure(settings, plan, root, prepared, exc)
        try:
            dataset = _verify(settings, str(plan["datasetRef"]), args)
        except Exception as verify_error:
            plan.update(
                status="DATASET_PREPARATION_FAILED",
                error=f"automatic prepare completed but dataset verification failed: {verify_error}",
                recommendedCommand="tq-research doctor",
                failureCount=1,
            )
            _write_matrix(root, plan)
            return 2
        plan["preparedAutomatically"] = True
    bound = replace(settings, qlib_data_uri=Path(str(dataset["path"])))
    for alpha in {str(job["alphaPack"]) for job in plan["jobs"]}:
        assert_alpha_pack_compatible(bound, ALPHA_PACKS[alpha])
    plan["dataset"] = dataset
    if args.dry_run:
        plan["status"] = "DRY_RUN"
        _write_matrix(root, plan)
        return 0

    failures = 0
    for job in plan["jobs"]:
        probe = [
            *_base(Path(str(job["config"]))),
            "runtime-probe",
            "--model-profile",
            str(job["modelProfile"]),
        ]
        code, runtime = _execute(
            probe,
            verbose=args.verbose_child_output,
            echo_stdout=args.verbose_child_output,
        )
        job["runtime"] = runtime
        if code:
            job.update(status="RUNTIME_UNAVAILABLE", exitCode=code)
            failures += 1
            if not args.continue_on_error:
                break
            continue
        code, result = _execute(list(job["command"]), verbose=args.verbose_child_output)
        result = _attach_summary(settings, job, result)
        job.update(status="SUCCEEDED" if code == 0 else "FAILED", exitCode=code, result=result)
        if code:
            failures += 1
            if not args.continue_on_error:
                break
            continue
        if plan["predictionBacktest"]:
            predictions = _predictions(result)
            if predictions:
                bt = [
                    *_base(Path(str(job["config"]))),
                    "backtest-predictions",
                    str(predictions),
                    "--dataset-ref",
                    str(plan["datasetRef"]),
                    "--benchmark",
                    args.benchmark,
                    "--artifact-level",
                    "minimal",
                ]
                if args.topn is not None:
                    bt.extend(["--topn", str(args.topn)])
                bt_code, bt_result = _execute(bt, verbose=args.verbose_child_output)
                backtest_payload: dict[str, Any] = {"exitCode": bt_code, "result": bt_result}
                bt_summary = summarize_result(settings.paths.output, bt_result)
                if bt_summary:
                    backtest_payload["summary"] = bt_summary
                    if bt_result is not None and bt_summary.get("manifest"):
                        bt_result.setdefault("manifest", str(bt_summary["manifest"]))
                job["predictionBacktest"] = backtest_payload
                if bt_code:
                    failures += 1
                    if not args.continue_on_error:
                        break
    plan["failureCount"] = failures
    plan["status"] = "SUCCEEDED" if failures == 0 else "PARTIAL" if args.continue_on_error else "FAILED"
    _write_matrix(root, plan)
    return 0 if failures == 0 else 2


def _output(settings: Settings, command: str, requested: str | None) -> Path:
    if requested:
        return Path(requested).expanduser().resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return settings.paths.output / "quickstart" / f"{stamp}-{command}"


def main() -> int:
    args = parser().parse_args()
    settings = Settings.load(
        args.config,
        require_tushare=args.command == "prepare" and args.source == "tushare",
        create_dirs=args.command != "doctor",
    )
    if args.command == "doctor":
        payload = doctor(settings, args)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["status"] == "READY" else 2
    if args.command == "prepare":
        payload = prepare(settings, args)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["status"] == "READY" else 2
    if args.command == "catalog":
        payload = catalog(settings)
        if args.as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            for alpha in payload["alphaPacks"]:
                print(f"alpha  {alpha['id']:<28} {alpha['handler']}")
            for model in payload["modelPresets"]:
                print(f"model  {model['name']:<28} {model['profile']}")
        return 0
    if args.command == "backtest":
        command = [
            *_base(settings.config_path),
            "backtest-predictions",
            args.predictions,
            "--dataset-ref",
            _dataset_ref(settings, args.dataset_ref),
            "--benchmark",
            args.benchmark,
            "--artifact-level",
            args.artifact_level,
        ]
        for flag, value in (
            ("--topn", args.topn),
            ("--n-drop", args.n_drop),
            ("--hold-thresh", args.hold_thresh),
        ):
            if value is not None:
                command.extend([flag, str(value)])
        return subprocess.run(command, check=False).returncode

    root = _output(settings, args.command, args.output)
    plan = build_plan(settings, args, root)
    if args.command == "plan":
        plan["status"] = "PLANNED"
        _write_matrix(root, plan)
        print(json.dumps({"output": str(root), "jobs": len(plan["jobs"])}, ensure_ascii=False))
        return 0
    code = run_plan(settings, args, plan, root)
    print(render_terminal_summary(plan, root))
    result_payload: dict[str, Any] = {
        "status": plan["status"],
        "output": str(root),
        "matrix": str(root / "research_matrix.json"),
        "summary": str(root / "research_matrix.md"),
    }
    for key in (
        "error",
        "recommendedCommand",
        "selectionCommand",
        "datasetRecoveryCommand",
        "retryCommand",
        "sourceReference",
        "sourceAction",
        "preparedAutomatically",
    ):
        if key in plan:
            result_payload[key] = plan[key]
    print(json.dumps(result_payload, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
