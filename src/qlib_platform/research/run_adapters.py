from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from qlib_platform.research.run_manifest import (
    artifact_record,
    build_run_manifest,
    canonical_business_value,
    config_identity,
    environment_identity,
    frozen_module_command,
    project_git_identity,
    store_root,
    write_run_manifest,
)
from qlib_platform.settings import Settings


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _dataset_evidence(data_path: str | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not data_path:
        return {}, []
    manifest_path = Path(data_path).expanduser().resolve() / "dataset_manifest.json"
    if not manifest_path.is_file():
        return {}, []
    payload = _load_json(manifest_path)
    semantic = payload.get("semantic_contract", {})
    semantic = semantic if isinstance(semantic, Mapping) else {}
    metadata = {
        "schema_version": payload.get("schema_version"),
        "version_id": payload.get("version_id"),
        "data_release_id": payload.get("data_release_id") or semantic.get("data_release_id"),
        "calendar": payload.get("calendar") or semantic.get("calendar"),
        "coverage": payload.get("coverage") or semantic.get("coverage"),
        "source_snapshot_id": payload.get("source_snapshot_id"),
        "watermark": payload.get("watermark") or semantic.get("watermark"),
        "universe_membership_sha256": payload.get("universe_membership_sha256"),
    }
    return metadata, [artifact_record(manifest_path, role="dataset", semantic_metadata=metadata)]


def _artifact_from_manifest(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    payload = _load_json(path)
    if not payload:
        return [], []
    artifacts = [artifact_record(path, role="manifest", semantic_metadata={
        "schemaVersion": payload.get("schemaVersion") or payload.get("schema_version"),
        "runId": payload.get("runId") or payload.get("run_id"),
        "status": payload.get("status"),
    })]
    for item in payload.get("artifacts", []):
        if not isinstance(item, Mapping):
            continue
        raw = item.get("localPath") or item.get("local_path") or item.get("path")
        if not raw:
            continue
        candidate = Path(str(raw)).expanduser()
        if candidate.is_file():
            artifacts.append(
                artifact_record(
                    candidate,
                    semantic_metadata={
                        "artifact_type": item.get("artifactType") or item.get("type"),
                        "name": item.get("name"),
                    },
                )
            )
    return artifacts, [payload]


def _find_mapping(payloads: Sequence[Mapping[str, Any]], keys: Sequence[str]) -> Mapping[str, Any] | None:
    for payload in payloads:
        stack: list[Any] = [payload]
        while stack:
            current = stack.pop()
            if not isinstance(current, Mapping):
                continue
            for key in keys:
                value = current.get(key)
                if isinstance(value, Mapping):
                    return value
            stack.extend(value for value in current.values() if isinstance(value, Mapping))
    return None


def _find_value(payloads: Sequence[Mapping[str, Any]], keys: Sequence[str]) -> Any:
    for payload in payloads:
        stack: list[Any] = [payload]
        while stack:
            current = stack.pop()
            if not isinstance(current, Mapping):
                continue
            for key in keys:
                value = current.get(key)
                if value not in {None, ""} and not isinstance(value, (Mapping, list, tuple)):
                    return value
            stack.extend(value for value in current.values() if isinstance(value, Mapping))
    return None


def record_quickstart_run(
    settings: Settings,
    plan: Mapping[str, Any],
    root: Path,
    *,
    argv: Sequence[str],
) -> Path:
    research_spec = plan.get("researchSpec", {})
    research_spec = research_spec if isinstance(research_spec, Mapping) else {}
    research = research_spec.get("research", {})
    research = research if isinstance(research, Mapping) else {}
    dataset = plan.get("dataset", {})
    dataset = dataset if isinstance(dataset, Mapping) else {}
    dataset_spec = research_spec.get("dataset", {})
    dataset_spec = dataset_spec if isinstance(dataset_spec, Mapping) else {}

    artifacts: list[dict[str, Any]] = []
    matrix_path = root / "research_matrix.json"
    if matrix_path.is_file():
        artifacts.append(artifact_record(matrix_path, role="evidence", semantic_metadata={
            "schemaVersion": plan.get("schemaVersion"),
            "researchId": plan.get("researchId"),
            "status": plan.get("status"),
        }))
    state_path = root / "run_state.json"
    if state_path.is_file():
        artifacts.append(artifact_record(state_path, role="evidence"))

    dataset_meta, dataset_artifacts = _dataset_evidence(str(dataset.get("path") or ""))
    artifacts.extend(dataset_artifacts)
    child_payloads: list[dict[str, Any]] = []
    for job in plan.get("jobs", []):
        if not isinstance(job, Mapping):
            continue
        manifests: list[Path] = []
        result = job.get("result")
        if isinstance(result, Mapping) and result.get("manifest"):
            manifests.append(Path(str(result["manifest"])).expanduser())
        backtest = job.get("predictionBacktest")
        if isinstance(backtest, Mapping):
            result = backtest.get("result")
            if isinstance(result, Mapping) and result.get("manifest"):
                manifests.append(Path(str(result["manifest"])).expanduser())
        for manifest_path in manifests:
            found, payloads = _artifact_from_manifest(manifest_path)
            artifacts.extend(found)
            child_payloads.extend(payloads)

    experiment = settings.data.get("experiment", {})
    experiment = experiment if isinstance(experiment, Mapping) else {}
    universe = _find_mapping(child_payloads, ("universe", "universeSpec", "universe_spec")) or {}
    label = _find_mapping(child_payloads, ("labelSpec", "label_spec", "label")) or {}
    market = _find_mapping(child_payloads, ("marketRuleSet", "market_rule_set", "marketRules")) or {}
    feature_snapshot = _find_value(
        child_payloads, ("featureSnapshotId", "feature_snapshot_id", "featureSnapshot")
    )
    prediction_snapshot = _find_value(
        child_payloads, ("predictionSnapshotId", "prediction_snapshot_id", "snapshotId")
    )
    model_digests = [item["content_sha256"] for item in artifacts if item["role"] == "model"]
    prediction_digests = [item["content_sha256"] for item in artifacts if item["role"] == "prediction"]

    portfolio = research.get("portfolio", {})
    portfolio = portfolio if isinstance(portfolio, Mapping) else {}
    components = {
        "code": project_git_identity(),
        "resolved_config": config_identity(settings),
        "environment": environment_identity(),
        "data": {
            "provider": canonical_business_value(settings.data.get("data_source", {})),
            "data_release_id": dataset.get("dataReleaseId") or dataset_spec.get("dataReleaseId"),
            "dataset_version_id": dataset.get("versionId") or dataset_spec.get("versionId"),
            "verification": dataset.get("verification"),
            **dataset_meta,
        },
        "universe": universe or canonical_business_value(settings.data.get("universe", {})),
        "feature": {
            "alpha_packs": research.get("alphaPacks", []),
            "feature_snapshot_id": feature_snapshot,
            "materialization": [
                {
                    "alphaPack": job.get("alphaPack"),
                    "configSha256": job.get("configSha256"),
                    "scientificInputHash": job.get("scientificInputHash"),
                }
                for job in plan.get("jobs", [])
                if isinstance(job, Mapping)
            ],
        },
        "label": label or canonical_business_value(experiment.get("label", {})),
        "split": {
            **dict(research.get("split", {}) if isinstance(research.get("split"), Mapping) else {}),
            "labelIsolation": research.get("labelIsolation"),
        },
        "model": {
            "profiles": research.get("models", []),
            "artifact_digests": model_digests,
        },
        "prediction": {
            "snapshot_id": prediction_snapshot,
            "artifact_digests": prediction_digests,
        },
        "portfolio": dict(portfolio),
        "market": market
        or canonical_business_value(
            settings.data.get("market_rules", settings.data.get("backtest", {}))
        ),
        "benchmark": {"identity": portfolio.get("benchmark")},
    }
    immutable_dataset = str(components["data"].get("dataset_version_id") or "") or None
    reproduction = frozen_module_command(
        "qlib_platform.research.workflow.governed_entrypoint",
        argv,
        dataset_version_id=immutable_dataset,
        output_flag="--output",
    )
    manifest = build_run_manifest(
        source_kind="governed-quickstart",
        status=str(plan.get("status") or "UNKNOWN"),
        components=components,
        artifacts=artifacts,
        gates={"governance": plan.get("governance", {})},
        warnings=[str(item) for item in plan.get("observedWarnings", [])],
        known_deviations=[
            str(note)
            for job in plan.get("jobs", [])
            if isinstance(job, Mapping)
            for note in job.get("parityNotes", [])
        ],
        reproduction=reproduction,
    )
    return write_run_manifest(manifest, local_root=root, store=store_root(settings))


def record_official_parity_run(
    settings: Settings,
    output_root: Path,
    *,
    argv: Sequence[str],
    status: str | None = None,
) -> Path:
    report_path = output_root / "parity_report.json"
    plan_path = output_root / "plan.json"
    report = _load_json(report_path)
    plan = _load_json(plan_path)
    effective_status = status or str(report.get("status") or "FAILED")

    artifacts: list[dict[str, Any]] = []
    for path, role in (
        (plan_path, "evidence"),
        (report_path, "backtest"),
        (output_root / "parity_report.md", "evidence"),
        (output_root / "runtime_workflow.yaml", "evidence"),
    ):
        if path.is_file():
            artifacts.append(artifact_record(path, role=role))
    dataset_info = report.get("dataset", plan.get("dataset", {}))
    dataset_info = dataset_info if isinstance(dataset_info, Mapping) else {}
    dataset_meta, dataset_artifacts = _dataset_evidence(str(dataset_info.get("provider_uri") or ""))
    artifacts.extend(dataset_artifacts)

    for candidate in output_root.rglob("*"):
        if candidate.is_file() and candidate.name != "run_manifest.json":
            artifacts.append(artifact_record(candidate))

    environment = report.get("environment", {})
    environment = environment if isinstance(environment, Mapping) else environment_identity()
    code = environment.get("git", project_git_identity()) if isinstance(environment, Mapping) else {}
    components = {
        "code": code,
        "resolved_config": config_identity(settings),
        "environment": environment,
        "data": {
            "provider": "qlib",
            "data_release_id": dataset_info.get("data_release_id"),
            "dataset_version_id": dataset_info.get("dataset_version_id"),
            "manifest_sha256": dataset_info.get("manifest_sha256"),
            **dataset_meta,
        },
        "universe": {
            "identity": "CSI300-PIT",
            "audit": (
                report.get("data_semantics", {}).get("universe")
                if isinstance(report.get("data_semantics"), Mapping)
                else None
            ),
        },
        "feature": {"alpha_pack": "Alpha158", "workflow": report.get("workflow", {})},
        "label": {"expression": "Ref($close,-2)/Ref($close,-1)-1", "horizon": "T+1"},
        "split": {"segments": plan.get("segments") or plan.get("workflow_segments")},
        "model": {
            "class": "qlib.contrib.model.gbdt.LGBModel",
            "parameters": plan.get("model") or report.get("model"),
            "seeds": report.get("seeds", []),
        },
        "prediction": {
            "engine_parity": report.get("engine_parity"),
            "vendor_parity": report.get("vendor_parity"),
        },
        "portfolio": {"strategy": "TopkDropoutStrategy", "topk": 50, "n_drop": 5},
        "market": {"profile": "qlib_official_parity_v1"},
        "benchmark": {"identity": "SH000300"},
    }
    reproduction = frozen_module_command(
        "qlib_platform.research.workflow.official_parity_entrypoint",
        argv,
        dataset_version_id=str(dataset_info.get("dataset_version_id") or "") or None,
        output_flag="--output-dir",
    )
    manifest = build_run_manifest(
        source_kind="official-parity",
        status=effective_status,
        components=components,
        artifacts=artifacts,
        gates={
            "engine_parity": report.get("engine_parity", {}),
            "vendor_parity": report.get("vendor_parity", {}),
            "golden_checks": report.get("golden_checks", {}),
        },
        known_deviations=[
            "official parity is research evidence; automatic model selection/promotion remain disabled"
        ],
        reproduction=reproduction,
    )
    return write_run_manifest(manifest, local_root=output_root, store=store_root(settings))


def record_daily_run(
    settings: Settings,
    daily_manifest_path: Path,
    *,
    argv: Sequence[str],
) -> Path:
    payload = _load_json(daily_manifest_path)
    root = daily_manifest_path.parent
    lineage = payload.get("lineage", {})
    lineage = lineage if isinstance(lineage, Mapping) else {}
    immutable = payload.get("immutable_input", {})
    immutable = immutable if isinstance(immutable, Mapping) else {}
    dataset = payload.get("dataset", {})
    dataset = dataset if isinstance(dataset, Mapping) else {}

    artifacts: list[dict[str, Any]] = [artifact_record(daily_manifest_path, role="manifest")]
    report_raw = payload.get("report")
    if report_raw and Path(str(report_raw)).is_file():
        artifacts.append(artifact_record(Path(str(report_raw)), role="backtest"))
    plan_raw = payload.get("plan")
    if plan_raw and Path(str(plan_raw)).is_file():
        artifacts.append(artifact_record(Path(str(plan_raw)), role="evidence"))
    dataset_meta, dataset_artifacts = _dataset_evidence(str(dataset.get("data_path") or ""))
    artifacts.extend(dataset_artifacts)

    steps = payload.get("steps", {})
    steps = steps if isinstance(steps, Mapping) else {}
    regression = steps.get("regression_backtest", {})
    regression = regression if isinstance(regression, Mapping) else {}
    regression_output = regression.get("output", {})
    regression_output = regression_output if isinstance(regression_output, Mapping) else {}
    regression_root_raw = regression_output.get("output_root")
    if regression_root_raw:
        matrix = Path(str(regression_root_raw)) / "research_matrix.json"
        if matrix.is_file():
            artifacts.append(artifact_record(matrix, role="evidence"))
        child_manifest = Path(str(regression_root_raw)) / "run_manifest.json"
        if child_manifest.is_file():
            artifacts.append(artifact_record(child_manifest, role="evidence"))

    provider = lineage.get("provider", {})
    provider = provider if isinstance(provider, Mapping) else {}
    components = {
        "code": lineage.get("code", payload.get("code", project_git_identity())),
        "resolved_config": config_identity(settings),
        "environment": environment_identity(),
        "data": {
            "provider": provider,
            "data_release_id": immutable.get("data_release_id") or dataset.get("data_release_id"),
            "dataset_version_id": immutable.get("dataset_version_id") or dataset.get("dataset_version_id"),
            "dataset_manifest_sha256": immutable.get("dataset_manifest_sha256")
            or dataset.get("dataset_manifest_sha256"),
            "watermark": provider.get("watermarks_at_plan"),
            **dataset_meta,
        },
        "universe": lineage.get("universe", {}),
        "feature": steps.get("feature_materialization", {}),
        "label": {"policy": "frozen-regression-template"},
        "split": {"target_session": lineage.get("target_session") or payload.get("target_session")},
        "model": lineage.get("model_policy", {}),
        "prediction": {"regression_status": regression.get("status")},
        "portfolio": {"policy": "frozen-regression-baseline"},
        "market": canonical_business_value(
            settings.data.get("market_rules", settings.data.get("backtest", {}))
        ),
        "benchmark": lineage.get("benchmark", {}),
    }
    reproduction = frozen_module_command(
        "qlib_platform.runtime.production_daily_run_entrypoint",
        argv,
    )
    manifest = build_run_manifest(
        source_kind="production-daily-run",
        status=str(payload.get("status") or "FAILED"),
        components=components,
        artifacts=artifacts,
        gates={"checkpoint_ledger": payload.get("checkpoint_ledger", {})},
        known_deviations=[
            "daily run is research evidence and does not authorize model promotion or live execution"
        ],
        reproduction=reproduction,
    )
    return write_run_manifest(manifest, local_root=root, store=store_root(settings))
