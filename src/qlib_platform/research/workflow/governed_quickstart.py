from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from qlib_platform.alpha.registry import ALPHA_PACKS, assert_alpha_pack_compatible
from qlib_platform.research.interfaces.cli_ux import render_terminal_summary, summarize_result
from qlib_platform.research.workflow import quickstart as legacy
from qlib_platform.research.workflow.control import (
    RunState,
    atomic_write_json,
    build_research_spec,
    enforce_governance,
    expand_matrix,
    file_sha256,
    identity,
    research_lock,
)
from qlib_platform.research.workflow.templates import get_research_template
from qlib_platform.settings import Settings

_RESEARCH_COMMANDS = {"plan", "run", "matrix", "baseline"}


def _state_text() -> str:
    candidates = (
        Path.cwd() / "docs" / "current_state.md",
        Path(__file__).resolve().parents[4] / "docs" / "current_state.md",
    )
    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return (
        "Formal candidates | Disallowed\n"
        "Model selection | Disallowed\n"
        "Final holdout | `SEALED`; access disallowed\n"
        "Publishing in Phase 3-D | Disabled\n"
    )


def _verification_contract(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "mode": args.verify_mode,
        "sampleSize": args.sample_size,
        "workers": args.workers,
        "resourceBudget": {
            "maxWorkers": args.workers,
            "maxConcurrentJobs": args.max_concurrent_jobs,
            "maxMemoryGb": args.max_memory_gb,
            "maxDiskGb": args.max_disk_gb,
            "policy": "serial research execution with explicit verification/resource caps",
        },
    }


def _scientific_spec(
    settings: Settings,
    args: argparse.Namespace,
    dataset_anchor: Mapping[str, Any] | None,
) -> dict[str, Any]:
    alphas, profiles = legacy._selected(args)
    template = get_research_template(getattr(args, "template", None))
    stage = "release" if args.mode == "walk-forward" else args.stage
    return build_research_spec(
        dataset_ref=legacy._dataset_ref(settings, args.dataset_ref),
        dataset_anchor=dataset_anchor,
        mode=args.mode,
        template=template.template_id if template is not None else None,
        stage=stage,
        alpha_packs=alphas,
        model_profiles=profiles,
        train=args.train,
        valid=args.valid,
        test=args.test,
        start=args.start,
        end=args.end,
        benchmark=args.benchmark,
        topn=args.topn,
        artifact_level=args.artifact_level,
        prediction_backtest=bool(
            args.mode == "fixed" and args.stage == "signal" and args.prediction_backtest
        ),
        verification=_verification_contract(args),
    )


def _default_root(settings: Settings, command: str, research_id: str, requested: str | None) -> Path:
    if requested:
        return Path(requested).expanduser().resolve()
    return settings.paths.output / "quickstart" / f"{research_id}-{command}"


def _read_only_dataset_anchor(
    settings: Settings,
    args: argparse.Namespace,
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        return legacy._verify(settings, legacy._dataset_ref(settings, args.dataset_ref), args), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _governed_plan(
    settings: Settings,
    args: argparse.Namespace,
) -> tuple[dict[str, Any], Path]:
    dataset_anchor, dataset_error = _read_only_dataset_anchor(settings, args)
    spec = _scientific_spec(settings, args, dataset_anchor)
    root = _default_root(settings, args.command, str(spec["researchId"]), args.output)
    plan = legacy.build_plan(settings, args, root)
    policy = enforce_governance(
        stage=str(spec["research"]["stage"]),
        mode=str(spec["research"]["mode"]),
        current_state_text=_state_text(),
    )
    alphas, profiles = legacy._selected(args)
    cells = expand_matrix(alphas, profiles, research_id=str(spec["researchId"]))
    by_pair = {(cell["alphaPack"], cell["model"]): cell for cell in cells}
    for job in plan["jobs"]:
        cell = by_pair[(job["alphaPack"], job["model"])]
        job["cellId"] = cell["cellId"]
        config_path = Path(str(job["config"]))
        job["configSha256"] = file_sha256(config_path)
        job["scientificInputHash"] = identity(
            {
                "cell": cell,
                "configSha256": job["configSha256"],
                "dataset": spec["dataset"],
                "mode": spec["research"]["mode"],
                "stage": spec["research"]["stage"],
                "split": spec["research"]["split"],
                "portfolio": spec["research"]["portfolio"],
                "artifactLevel": spec["research"]["artifactLevel"],
            },
            prefix="input-",
        )
    plan.update(
        schemaVersion="2.0",
        researchId=spec["researchId"],
        researchSpec=spec,
        governance=policy,
        datasetPlanStatus="READY" if dataset_anchor is not None else "UNAVAILABLE",
    )
    if dataset_anchor is not None:
        plan["dataset"] = dataset_anchor
    if dataset_error:
        plan["datasetPlanError"] = dataset_error
    plan["resourceBudget"] = dict(spec["verification"]["resourceBudget"])
    plan["failurePolicy"] = "continue" if args.continue_on_error else "stop"
    plan["stageModel"] = [
        "data.verify",
        "alpha.compatibility",
        "runtime.probe",
        "research.execute",
        "prediction.backtest",
        "review",
        "permitted.export",
    ]
    plan["resumeContract"] = {
        "state": str(root / "run_state.json"),
        "lock": str(root / ".research.lock"),
        "reuse": "SUCCEEDED stages only when input hash and output hashes match",
        "corruptOutput": "fail closed and rerun the affected stage",
    }
    return plan, root


def _stage_metadata(state: RunState, stage: str) -> Mapping[str, Any]:
    record = state.payload.get("stages", {}).get(stage, {})
    if not isinstance(record, Mapping):
        return {}
    metadata = record.get("metadata", {})
    return metadata if isinstance(metadata, Mapping) else {}


def _json_artifact(root: Path, stage: str, payload: Mapping[str, Any]) -> Path:
    safe = stage.replace("/", "_").replace(":", "_")
    path = root / "stage_evidence" / f"{safe}.json"
    atomic_write_json(path, payload)
    return path


def _run_stage(state: RunState, root: Path, stage: str, input_payload: Mapping[str, Any], operation):
    input_hash = identity(input_payload, prefix="stage-")
    decision = state.decide(stage, input_hash)
    if decision.reuse:
        return True, _stage_metadata(state, stage)
    state.start(stage, input_hash)
    try:
        status, metadata, extra_outputs = operation()
        evidence = _json_artifact(root, stage, metadata)
        state.finish(
            stage,
            status=status,
            output_paths=[evidence, *extra_outputs],
            metadata=metadata,
            error=None if status == "SUCCEEDED" else str(metadata.get("error") or status),
        )
        return False, metadata
    except Exception as exc:
        state.finish(
            stage,
            status="FAILED",
            metadata={"error": f"{type(exc).__name__}: {exc}"},
            error=f"{type(exc).__name__}: {exc}",
        )
        raise


def _manifest_outputs(result: Mapping[str, Any] | None) -> list[Path]:
    if not result or not result.get("manifest"):
        return []
    manifest = Path(str(result["manifest"])).expanduser()
    if not manifest.is_file():
        return []
    outputs = [manifest]
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return outputs
    for item in payload.get("artifacts", []):
        if isinstance(item, Mapping) and item.get("localPath"):
            artifact = Path(str(item["localPath"])).expanduser()
            if artifact.is_file():
                outputs.append(artifact)
    return outputs


def _run(settings: Settings, args: argparse.Namespace, plan: dict[str, Any], root: Path) -> int:
    state = RunState(root / "run_state.json", str(plan["researchId"]))
    failures = 0
    with research_lock(root / ".research.lock"):
        dataset_input = {
            "dataset": plan["researchSpec"]["dataset"],
            "verification": plan["researchSpec"]["verification"],
        }

        def verify_op():
            dataset = legacy._verify(settings, str(plan["datasetRef"]), args)
            return "SUCCEEDED", {"dataset": dataset}, []

        reused, dataset_meta = _run_stage(state, root, "data.verify", dataset_input, verify_op)
        dataset = dataset_meta.get("dataset")
        if not isinstance(dataset, Mapping):
            raise RuntimeError("verified dataset stage did not produce dataset metadata")
        plan["dataset"] = dict(dataset)
        plan["dataVerifyReused"] = reused

        bound = replace(settings, qlib_data_uri=Path(str(dataset["path"])))
        for alpha in sorted({str(job["alphaPack"]) for job in plan["jobs"]}):
            assert_alpha_pack_compatible(bound, ALPHA_PACKS[alpha])

        if args.dry_run:
            plan["status"] = "DRY_RUN"
            legacy._write_matrix(root, plan)
            return 0

        for job in plan["jobs"]:
            cell = str(job["cellId"])
            runtime_stage = f"job.{cell}.runtime"
            research_stage = f"job.{cell}.research"
            common_input = {
                "cellId": cell,
                "scientificInputHash": job["scientificInputHash"],
                "datasetVersionId": dataset.get("versionId"),
                "dataReleaseId": dataset.get("dataReleaseId"),
            }

            def runtime_op():
                probe = [
                    *legacy._base(Path(str(job["config"]))),
                    "runtime-probe",
                    "--model-profile",
                    str(job["modelProfile"]),
                ]
                code, runtime, warnings = legacy._execute(
                    probe,
                    verbose=args.verbose_child_output,
                    echo_stdout=args.verbose_child_output,
                )
                metadata = {"exitCode": code, "runtime": runtime, "warnings": warnings}
                return ("SUCCEEDED" if code == 0 else "FAILED"), metadata, []

            runtime_reused, runtime_meta = _run_stage(
                state, root, runtime_stage, {**common_input, "stage": "runtime"}, runtime_op
            )
            job["runtime"] = runtime_meta.get("runtime")
            job["runtimeReused"] = runtime_reused
            legacy._merge_warnings(job, [str(x) for x in runtime_meta.get("warnings", [])])
            if int(runtime_meta.get("exitCode", 1)) != 0:
                job.update(status="RUNTIME_UNAVAILABLE", exitCode=int(runtime_meta.get("exitCode", 2)))
                failures += 1
                if not args.continue_on_error:
                    break
                continue

            def research_op():
                code, result, warnings = legacy._execute(
                    list(job["command"]), verbose=args.verbose_child_output
                )
                result = legacy._attach_summary(settings, job, result)
                outputs = _manifest_outputs(result if isinstance(result, Mapping) else None)
                metadata = {"exitCode": code, "result": result, "warnings": warnings}
                return ("SUCCEEDED" if code == 0 else "FAILED"), metadata, outputs

            research_reused, research_meta = _run_stage(
                state, root, research_stage, {**common_input, "stage": "research"}, research_op
            )
            code = int(research_meta.get("exitCode", 1))
            result = research_meta.get("result")
            legacy._merge_warnings(job, [str(x) for x in research_meta.get("warnings", [])])
            job.update(
                status="REUSED" if research_reused and code == 0 else "SUCCEEDED" if code == 0 else "FAILED",
                exitCode=code,
                result=result,
                researchReused=research_reused,
            )
            if code:
                failures += 1
                if not args.continue_on_error:
                    break
                continue

            if plan["predictionBacktest"]:
                predictions = legacy._predictions(result if isinstance(result, Mapping) else None)
                if predictions:
                    bt_stage = f"job.{cell}.backtest"

                    def backtest_op():
                        command = [
                            *legacy._base(Path(str(job["config"]))),
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
                            command.extend(["--topn", str(args.topn)])
                        bt_code, bt_result, warnings = legacy._execute(
                            command, verbose=args.verbose_child_output
                        )
                        summary = summarize_result(settings.paths.output, bt_result)
                        metadata = {
                            "exitCode": bt_code,
                            "result": bt_result,
                            "summary": summary,
                            "warnings": warnings,
                        }
                        outputs = _manifest_outputs(
                            bt_result if isinstance(bt_result, Mapping) else None
                        )
                        return ("SUCCEEDED" if bt_code == 0 else "FAILED"), metadata, outputs

                    bt_reused, bt_meta = _run_stage(
                        state,
                        root,
                        bt_stage,
                        {**common_input, "stage": "prediction-backtest", "predictions": str(predictions)},
                        backtest_op,
                    )
                    job["predictionBacktest"] = {**dict(bt_meta), "reused": bt_reused}
                    legacy._merge_warnings(job, [str(x) for x in bt_meta.get("warnings", [])])
                    if int(bt_meta.get("exitCode", 1)) != 0:
                        failures += 1
                        if not args.continue_on_error:
                            break

        plan["failureCount"] = failures
        plan["status"] = (
            "SUCCEEDED" if failures == 0 else "PARTIAL" if args.continue_on_error else "FAILED"
        )
        plan["observedWarnings"] = list(
            dict.fromkeys(
                str(item)
                for job in plan["jobs"]
                for item in job.get("warnings", [])
            )
        )
        legacy._write_matrix(root, plan)
    return 0 if failures == 0 else 2


def _parser() -> argparse.ArgumentParser:
    parser = legacy.parser()
    subparsers = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )
    for name in _RESEARCH_COMMANDS:
        command = subparsers.choices[name]
        command.add_argument("--max-concurrent-jobs", type=int, default=1, choices=[1])
        command.add_argument("--max-memory-gb", type=float, default=16.0)
        command.add_argument("--max-disk-gb", type=float, default=50.0)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command not in _RESEARCH_COMMANDS:
        return legacy.main()

    settings = Settings.load(args.config, require_tushare=False, create_dirs=True)
    try:
        plan, root = _governed_plan(settings, args)
    except PermissionError as exc:
        print(json.dumps({"status": "REJECTED", "error": str(exc)}, ensure_ascii=False))
        return 2

    if args.command == "plan":
        plan["status"] = "PLANNED" if plan["datasetPlanStatus"] == "READY" else "BLOCKED"
        legacy._write_matrix(root, plan)
        print(
            json.dumps(
                {
                    "status": plan["status"],
                    "researchId": plan["researchId"],
                    "output": str(root),
                    "jobs": len(plan["jobs"]),
                    "datasetPlanError": plan.get("datasetPlanError"),
                },
                ensure_ascii=False,
            )
        )
        return 0 if plan["status"] == "PLANNED" else 2

    try:
        code = _run(settings, args, plan, root)
    except (RuntimeError, PermissionError, ValueError) as exc:
        plan.update(status="REJECTED", error=f"{type(exc).__name__}: {exc}", failureCount=1)
        legacy._write_matrix(root, plan)
        code = 2

    print(render_terminal_summary(plan, root))
    print(
        json.dumps(
            {
                "status": plan["status"],
                "researchId": plan["researchId"],
                "output": str(root),
                "matrix": str(root / "research_matrix.json"),
                "runState": str(root / "run_state.json"),
            },
            ensure_ascii=False,
        )
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
