from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from qlib_platform.lineage import git_revision, sha256_json
from qlib_platform.artifacts.prediction_snapshot import _identity
from qlib_platform.data.store import sha256_file
from qlib_platform.research.phase2_program import PHASE2_INCREMENTAL_CANDIDATE_FAMILY
from qlib_platform.research.phase3_contract import (
    PHASE2_EVIDENCE_SCHEMA,
    _contains_final_holdout,
    _mapping,
    _sequence,
    _validate_phase2_acceptance,
    _validate_data_release_acceptance,
    load_phase3_contract,
    load_phase3_lock,
)
from qlib_platform.research.phase3_diagnostics import (
    PHASE3_DIAGNOSTICS_SCHEMA,
    PHASE3_EVIDENCE_INDEX_SCHEMA,
    PHASE3_MANIFEST_NAME,
    _expected_artifact_names,
)
from qlib_platform.research.phase3_program import PHASE3_EXECUTION_ORDER, load_phase3_plan
from qlib_platform.research.regime import load_regime_spec


PHASE3_PORTABLE_EVIDENCE_SCHEMA = "phase3_portable_evidence_v1"
PHASE3_PORTABLE_EVIDENCE_MANIFEST = "phase3_portable_evidence.json"


def _load_json(path: Path, name: str) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError(f"{name} is missing or unsafe: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{name} must be a JSON object")
    return payload


def _resolve_file(path: str | Path, name: str) -> Path:
    target = Path(path).expanduser().resolve()
    if not target.is_file() or target.is_symlink():
        raise FileNotFoundError(f"{name} is missing or unsafe: {target}")
    return target


def _inside(root: Path, target: Path, name: str) -> Path:
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{name} escapes its declared root") from exc
    return target


def _resolve_relative(base: Path, value: object, name: str) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{name} path is required")
    candidate = Path(raw).expanduser()
    target = (candidate if candidate.is_absolute() else base / candidate).resolve()
    return _resolve_file(target, name)


def _snapshot_payload(snapshot_path: Path) -> Path:
    snapshot = _load_json(snapshot_path, "PredictionSnapshot manifest")
    payload = _mapping(snapshot.get("payload"), "PredictionSnapshot payload")
    raw = str(payload.get("path") or "").strip()
    if not raw:
        raise ValueError("PredictionSnapshot payload path is required")
    target = (snapshot_path.parent / raw).resolve()
    if target.parent != snapshot_path.parent:
        raise ValueError("PredictionSnapshot payload escapes its manifest directory")
    return _resolve_file(target, "PredictionSnapshot payload")


def _run_snapshot(run_path: Path) -> Path:
    run = _load_json(run_path, "Phase 2 run manifest")
    for raw in _sequence(run.get("artifacts"), "Phase 2 run artifacts"):
        artifact = _mapping(raw, "Phase 2 run artifact")
        if artifact.get("name") == "oos_predictions.snapshot.json":
            return _resolve_relative(
                run_path.parent, artifact.get("localPath"), "PredictionSnapshot manifest"
            )
    raise FileNotFoundError("Phase 2 run does not contain oos_predictions.snapshot.json")


def _release_files(release_path: Path, data_root: Path) -> list[tuple[str, Path]]:
    release = _load_json(release_path, "DataRelease manifest")
    root = data_root.expanduser().resolve()
    if not root.is_dir() or root.is_symlink():
        raise ValueError("DataRelease data root is missing or unsafe")
    result: list[tuple[str, Path]] = []
    for raw_component in _sequence(release.get("components"), "DataRelease components"):
        component = _mapping(raw_component, "DataRelease component")
        role = str(component.get("role") or "").strip()
        if not role:
            raise ValueError("DataRelease component role is required")
        for raw_file in _sequence(component.get("files"), f"DataRelease {role} files"):
            entry = _mapping(raw_file, f"DataRelease {role} file")
            raw_path = Path(str(entry.get("path") or "").strip()).expanduser()
            if not str(raw_path):
                raise ValueError(f"DataRelease {role} file path is required")
            candidates = (
                [raw_path] if raw_path.is_absolute() else [release_path.parent / raw_path, root / raw_path]
            )
            resolved: list[Path] = []
            for candidate in candidates:
                target = candidate.resolve()
                if target.is_file() and not target.is_symlink():
                    _inside(root, target, f"DataRelease {role} file")
                    if target not in resolved:
                        resolved.append(target)
            if len(resolved) != 1:
                raise FileNotFoundError(f"DataRelease {role} file cannot be resolved uniquely: {raw_path}")
            result.append((role, resolved[0]))
    return result


def _copy_inventory_file(
    source: Path,
    *,
    logical_name: str,
    files: dict[str, dict[str, Any]],
    logical: dict[str, str],
    building: Path,
) -> str:
    source = _resolve_file(source, logical_name)
    source_path = str(source)
    digest = sha256_file(source)
    destination = building / "payload" / digest
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        if sha256_file(destination) != digest or destination.stat().st_size != source.stat().st_size:
            raise RuntimeError(f"portable evidence copy checksum mismatch: {logical_name}")
    files.setdefault(
        source_path,
        {
            "sourcePath": source_path,
            "payloadPath": f"payload/{digest}",
            "sha256": digest,
            "sizeBytes": source.stat().st_size,
        },
    )
    if logical_name in logical:
        raise ValueError(f"duplicate portable evidence logical name: {logical_name}")
    logical[logical_name] = source_path
    return source_path


def _add_phase2_runs(
    evidence_path: Path,
    evidence: Mapping[str, Any],
    *,
    files: dict[str, dict[str, Any]],
    logical: dict[str, str],
    references: dict[str, Any],
    building: Path,
) -> None:
    ablations = _mapping(evidence.get("ablationExperiments"), "Phase 2 ablation experiments")
    snapshots: dict[str, str] = {}
    for experiment_id, raw_experiment in sorted(ablations.items()):
        experiment = _mapping(raw_experiment, f"Phase 2 experiment {experiment_id}")
        manifests = _sequence(experiment.get("runManifests"), f"Phase 2 experiment {experiment_id} runs")
        for position, raw_run in enumerate(manifests):
            run_path = _resolve_relative(evidence_path.parent, raw_run, "Phase 2 run manifest")
            key = f"phase2Run:{experiment_id}:{position}"
            _copy_inventory_file(run_path, logical_name=key, files=files, logical=logical, building=building)
            snapshot_path = _run_snapshot(run_path)
            snapshot_key = str(snapshot_path)
            if snapshot_key not in snapshots:
                _copy_inventory_file(
                    snapshot_path,
                    logical_name=f"predictionSnapshot:{len(snapshots)}",
                    files=files,
                    logical=logical,
                    building=building,
                )
                payload_path = _snapshot_payload(snapshot_path)
                payload_source = _copy_inventory_file(
                    payload_path,
                    logical_name=f"predictionPayload:{len(snapshots)}",
                    files=files,
                    logical=logical,
                    building=building,
                )
                snapshots[snapshot_key] = payload_source
    references["predictionPayloads"] = snapshots


def export_phase3_portable_evidence(
    *,
    contract_lock: str | Path,
    plan_path: str | Path,
    diagnosis: str | Path,
    contract_path: str | Path,
    data_root: str | Path,
    output: str | Path,
) -> Path:
    """Create an immutable, content-addressed, read-only Phase 3 evidence package.

    The exported directory is intentionally outside the repository. Its immutable
    lock retains original paths as provenance, while the package manifest maps
    every required byte stream to a relative SHA-256-addressed payload file.
    """

    lock_path = _resolve_file(contract_lock, "Phase 3 design lock")
    plan_source = _resolve_file(plan_path, "Phase 3 diagnostic plan")
    diagnosis_source = Path(diagnosis).expanduser().resolve()
    diagnosis_root = (
        diagnosis_source.parent if diagnosis_source.name == PHASE3_MANIFEST_NAME else diagnosis_source
    )
    if not diagnosis_root.is_dir() or diagnosis_root.is_symlink():
        raise ValueError("Phase 3 diagnosis directory is missing or unsafe")
    contract_source = _resolve_file(contract_path, "Phase 3 contract")
    target = Path(output).expanduser().resolve()
    if target.exists():
        raise ValueError("portable evidence output must be a new directory")
    project_root = Path(__file__).resolve().parents[3]
    try:
        target.relative_to(project_root)
    except ValueError:
        pass
    else:
        raise ValueError("portable evidence output must be outside the source repository")

    lock = load_phase3_lock(lock_path)
    plan = load_phase3_plan(plan_source, contract_lock_sha256=str(lock["lockSha256"]))
    contract = load_phase3_contract(contract_source)
    locked_contract = _mapping(lock.get("contract"), "Phase 3 locked contract")
    if contract.file_sha256 != locked_contract.get(
        "file_sha256"
    ) or contract.semantic_sha256 != locked_contract.get("semantic_sha256"):
        raise ValueError("Phase 3 contract differs from the design lock")

    building_parent = target.parent
    building_parent.mkdir(parents=True, exist_ok=True)
    building = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=building_parent))
    try:
        files: dict[str, dict[str, Any]] = {}
        logical: dict[str, str] = {}
        references: dict[str, Any] = {}
        _copy_inventory_file(
            lock_path, logical_name="designLock", files=files, logical=logical, building=building
        )
        _copy_inventory_file(
            plan_source, logical_name="diagnosticPlan", files=files, logical=logical, building=building
        )
        _copy_inventory_file(
            contract_source, logical_name="phase3Contract", files=files, logical=logical, building=building
        )

        entry = _mapping(lock.get("entryCondition"), "Phase 3 entry condition")
        entry_names = {
            "phase2Acceptance": "phase2Acceptance",
            "phase2CandidateMetrics": "phase2CandidateMetrics",
            "phase2Evidence": "phase2Evidence",
            "dataReleaseAcceptance": "dataReleaseAcceptance",
        }
        entry_paths: dict[str, Path] = {}
        for lock_key, logical_name in entry_names.items():
            item = _mapping(entry.get(lock_key), lock_key)
            source = _resolve_file(str(item.get("path") or ""), logical_name)
            entry_paths[lock_key] = source
            _copy_inventory_file(
                source, logical_name=logical_name, files=files, logical=logical, building=building
            )

        evidence_path = entry_paths["phase2Evidence"]
        evidence = _load_json(evidence_path, "Phase 2 evidence index")
        collector = _load_json(entry_paths["phase2CandidateMetrics"], "Phase 2 candidate metrics")
        collector_lock = _mapping(collector.get("contractLock"), "Phase 2 collector contract lock")
        collector_lock_raw = str(collector_lock.get("path") or "").strip()
        if collector_lock_raw:
            collector_lock_path = _resolve_file(collector_lock_raw, "Phase 2 contract lock")
            _copy_inventory_file(
                collector_lock_path,
                logical_name="phase2ContractLock",
                files=files,
                logical=logical,
                building=building,
            )

        release_path = _resolve_relative(
            evidence_path.parent, evidence.get("dataReleaseManifest"), "DataRelease manifest"
        )
        _copy_inventory_file(
            release_path, logical_name="dataReleaseManifest", files=files, logical=logical, building=building
        )
        release_files: list[dict[str, str]] = []
        for role, source in _release_files(release_path, Path(data_root)):
            source_path = _copy_inventory_file(
                source,
                logical_name=f"dataRelease:{role}:{len(release_files)}",
                files=files,
                logical=logical,
                building=building,
            )
            release_files.append({"role": role, "sourcePath": source_path})
        references["dataReleaseFiles"] = release_files

        feature_raw = Path(str(evidence.get("featureSnapshot") or "").strip()).expanduser()
        feature_reference = (
            feature_raw if feature_raw.is_absolute() else evidence_path.parent / feature_raw
        ).resolve()
        if not feature_reference.exists() or feature_reference.is_symlink():
            raise FileNotFoundError(f"FeatureSnapshot is missing or unsafe: {feature_reference}")
        feature_manifest = (
            feature_reference / "manifest.json" if feature_reference.is_dir() else feature_reference
        )
        feature_manifest = _resolve_file(feature_manifest, "FeatureSnapshot manifest")
        _copy_inventory_file(
            feature_manifest, logical_name="featureSnapshot", files=files, logical=logical, building=building
        )
        feature_payload = _load_json(feature_manifest, "FeatureSnapshot manifest")
        feature_files: list[str] = []
        for raw in _sequence(feature_payload.get("files"), "FeatureSnapshot files"):
            item = _mapping(raw, "FeatureSnapshot file")
            partition = (feature_manifest.parent / str(item.get("name") or "")).resolve()
            if partition.parent != feature_manifest.parent:
                raise ValueError("FeatureSnapshot partition escapes its root")
            feature_files.append(
                _copy_inventory_file(
                    partition,
                    logical_name=f"featurePartition:{len(feature_files)}",
                    files=files,
                    logical=logical,
                    building=building,
                )
            )
        references["featurePartitions"] = feature_files
        labels_path = _resolve_relative(evidence_path.parent, evidence.get("labels"), "Phase 2 labels")
        _copy_inventory_file(
            labels_path, logical_name="phase2Labels", files=files, logical=logical, building=building
        )
        benchmark_path = _resolve_relative(
            evidence_path.parent, evidence.get("benchmarkFactorPanel"), "Phase 2 benchmark factor panel"
        )
        _copy_inventory_file(
            benchmark_path,
            logical_name="benchmarkFactorPanel",
            files=files,
            logical=logical,
            building=building,
        )
        _add_phase2_runs(
            evidence_path,
            evidence,
            files=files,
            logical=logical,
            references=references,
            building=building,
        )

        lineage = _mapping(lock.get("lineage"), "Phase 3 lineage")
        regime_path = _resolve_file(
            str(_mapping(lineage.get("regimeSpec"), "regime spec").get("path") or ""), "regime spec"
        )
        _copy_inventory_file(
            regime_path, logical_name="regimeSpec", files=files, logical=logical, building=building
        )

        diagnosis_index = _resolve_file(diagnosis_root / PHASE3_MANIFEST_NAME, "Phase 3 evidence index")
        _copy_inventory_file(
            diagnosis_index,
            logical_name="diagnosisEvidenceIndex",
            files=files,
            logical=logical,
            building=building,
        )
        diagnosis_payload = _load_json(diagnosis_index, "Phase 3 evidence index")
        diagnosis_artifacts: dict[str, str] = {}
        for raw in _sequence(diagnosis_payload.get("artifacts"), "Phase 3 diagnosis artifacts"):
            artifact = _mapping(raw, "Phase 3 diagnosis artifact")
            name = str(artifact.get("path") or "")
            artifact_path = (diagnosis_root / name).resolve()
            if artifact_path.parent != diagnosis_root:
                raise ValueError("Phase 3 diagnosis artifact escapes its root")
            diagnosis_artifacts[name] = _copy_inventory_file(
                artifact_path,
                logical_name=f"diagnosisArtifact:{name}",
                files=files,
                logical=logical,
                building=building,
            )
        references["diagnosisArtifacts"] = diagnosis_artifacts

        manifest: dict[str, Any] = {
            "schemaVersion": PHASE3_PORTABLE_EVIDENCE_SCHEMA,
            "programId": lock["programId"],
            "contractLockSha256": lock["lockSha256"],
            "planSha256": plan["planSha256"],
            "sourceCodeCommit": _mapping(lineage, "Phase 3 lineage").get("sourceCodeCommit"),
            "inputs": logical,
            "references": references,
            "files": sorted(files.values(), key=lambda item: str(item["sourcePath"])),
        }
        manifest["packageSha256"] = sha256_json(manifest)
        (building / PHASE3_PORTABLE_EVIDENCE_MANIFEST).write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
        )
        os.replace(building, target)
        return target / PHASE3_PORTABLE_EVIDENCE_MANIFEST
    finally:
        if building.exists():
            shutil.rmtree(building, ignore_errors=True)


def _load_portable(package_root: str | Path) -> tuple[Path, dict[str, Any], dict[str, Path]]:
    root = Path(package_root).expanduser().resolve()
    if not root.is_dir() or root.is_symlink():
        raise ValueError("portable evidence package root is missing or unsafe")
    manifest_path = root / PHASE3_PORTABLE_EVIDENCE_MANIFEST
    manifest = _load_json(manifest_path, "portable evidence package manifest")
    if manifest.get("schemaVersion") != PHASE3_PORTABLE_EVIDENCE_SCHEMA:
        raise ValueError("unsupported portable evidence package schema")
    recorded = str(manifest.get("packageSha256") or "")
    if recorded != sha256_json({key: value for key, value in manifest.items() if key != "packageSha256"}):
        raise ValueError("portable evidence package checksum mismatch")
    entries = _sequence(manifest.get("files"), "portable evidence package files")
    payloads: dict[str, Path] = {}
    expected_files = {PHASE3_PORTABLE_EVIDENCE_MANIFEST}
    for raw in entries:
        entry = _mapping(raw, "portable evidence package file")
        source = str(entry.get("sourcePath") or "")
        digest = str(entry.get("sha256") or "").lower()
        payload_name = str(entry.get("payloadPath") or "")
        if not source or len(digest) != 64 or payload_name != f"payload/{digest}":
            raise ValueError("portable evidence package file entry is invalid")
        if source in payloads:
            raise ValueError("portable evidence package source path is duplicated")
        target = (root / payload_name).resolve()
        _inside(root, target, "portable evidence payload")
        if target.parent != (root / "payload").resolve() or not target.is_file() or target.is_symlink():
            raise ValueError("portable evidence payload path is invalid")
        if sha256_file(target) != digest or target.stat().st_size != int(entry.get("sizeBytes", -1)):
            raise ValueError("portable evidence payload checksum or size mismatch")
        payloads[source] = target
        expected_files.add(payload_name)
    actual_files: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("portable evidence package must not contain symlinks")
        if path.is_file():
            actual_files.add(path.relative_to(root).as_posix())
    if actual_files != expected_files:
        raise ValueError("portable evidence package contains missing or unexpected files")
    return root, manifest, payloads


def _package_input(manifest: Mapping[str, Any], payloads: Mapping[str, Path], name: str) -> Path:
    inputs = _mapping(manifest.get("inputs"), "portable evidence inputs")
    source = str(inputs.get(name) or "")
    target = payloads.get(source)
    if target is None:
        raise ValueError(f"portable evidence package input is missing: {name}")
    return target


def _verify_release(
    release_path: Path,
    *,
    references: Mapping[str, Any],
    payloads: Mapping[str, Path],
) -> dict[str, Any]:
    release = _load_json(release_path, "portable DataRelease manifest")
    from qlib_platform.releases.capabilities import assert_manifest_capability

    assert_manifest_capability(release, "phase3")
    if str(release.get("manifestSha256") or "") != sha256_json(
        {key: value for key, value in release.items() if key != "manifestSha256"}
    ):
        raise ValueError("portable DataRelease manifest checksum mismatch")
    identity = {
        key: value
        for key, value in release.items()
        if key not in {"dataReleaseId", "identitySha256", "manifestSha256", "publishedAt"}
    }
    identity_sha = sha256_json(identity)
    if release.get("identitySha256") != identity_sha or release.get("dataReleaseId") != f"ds_{identity_sha}":
        raise ValueError("portable DataRelease identity mismatch")
    expected: list[tuple[str, Mapping[str, Any]]] = []
    for raw_component in _sequence(release.get("components"), "portable DataRelease components"):
        component = _mapping(raw_component, "portable DataRelease component")
        role = str(component.get("role") or "")
        component_identity = {
            key: component.get(key)
            for key in ("role", "componentReleaseId", "datasetKey", "schemaVersion", "coverage", "files")
        }
        if component.get("componentSha256") != sha256_json(component_identity):
            raise ValueError(f"portable DataRelease component checksum mismatch: {role}")
        for raw_file in _sequence(component.get("files"), f"portable DataRelease {role} files"):
            expected.append((role, _mapping(raw_file, f"portable DataRelease {role} file")))
    recorded_files = _sequence(references.get("dataReleaseFiles"), "portable DataRelease file references")
    if len(recorded_files) != len(expected):
        raise ValueError("portable DataRelease file inventory is incomplete")
    for (role, expected_file), raw_recorded in zip(expected, recorded_files, strict=True):
        recorded = _mapping(raw_recorded, "portable DataRelease file reference")
        source = str(recorded.get("sourcePath") or "")
        target = payloads.get(source)
        if recorded.get("role") != role or target is None:
            raise ValueError("portable DataRelease file reference mismatch")
        if sha256_file(target) != expected_file.get("sha256") or target.stat().st_size != int(
            expected_file.get("sizeBytes", -1)
        ):
            raise ValueError("portable DataRelease component file checksum or size mismatch")
    return release


def _verify_feature_snapshot(
    feature_path: Path,
    *,
    references: Mapping[str, Any],
    payloads: Mapping[str, Path],
) -> dict[str, Any]:
    feature = _load_json(feature_path, "portable FeatureSnapshot manifest")
    expected_files = [
        _mapping(raw, "portable FeatureSnapshot file")
        for raw in _sequence(feature.get("files"), "FeatureSnapshot files")
    ]
    actual_sources = [
        str(raw) for raw in _sequence(references.get("featurePartitions"), "FeatureSnapshot references")
    ]
    if len(actual_sources) != len(expected_files):
        raise ValueError("portable FeatureSnapshot file inventory is incomplete")
    for item, source in zip(expected_files, actual_sources, strict=True):
        target = payloads.get(source)
        if target is None or sha256_file(target) != item.get("sha256"):
            raise ValueError("portable FeatureSnapshot partition checksum mismatch")
    expected_id = "fs_" + sha256_json(
        {
            "featureRecipeId": feature.get("featureRecipeId"),
            "coverage": feature.get("coverage"),
            "files": feature.get("files"),
        }
    )
    if feature.get("featureSnapshotId") != expected_id:
        raise ValueError("portable FeatureSnapshot identity mismatch")
    return feature


def _verify_prediction_snapshot(
    snapshot_path: Path,
    payload_path: Path,
    *,
    expected_contract: Mapping[str, Any],
) -> pd.DataFrame:
    snapshot = _load_json(snapshot_path, "portable PredictionSnapshot manifest")
    if snapshot.get("snapshotId") != "ps_" + sha256_json(_identity(snapshot)):
        raise ValueError("portable PredictionSnapshot identity mismatch")
    payload = _mapping(snapshot.get("payload"), "portable PredictionSnapshot payload")
    if sha256_file(payload_path) != payload.get("sha256"):
        raise ValueError("portable PredictionSnapshot payload checksum mismatch")
    frame = pd.read_parquet(payload_path)
    if not isinstance(frame.index, pd.MultiIndex) or frame.index.names != ["datetime", "instrument"]:
        raise ValueError("portable PredictionSnapshot index is invalid")
    if frame.empty or frame.index.has_duplicates or list(frame.columns) != payload.get("columns"):
        raise ValueError("portable PredictionSnapshot payload schema is invalid")
    if len(frame) != int(payload.get("rows", -1)):
        raise ValueError("portable PredictionSnapshot row count mismatch")
    dates = pd.DatetimeIndex(frame.index.get_level_values("datetime")).normalize()
    coverage = {"startDate": str(dates.min().date()), "endDate": str(dates.max().date())}
    if coverage != payload.get("coverage") or "label" not in frame:
        raise ValueError("portable PredictionSnapshot coverage or embedded labels are invalid")
    contract = _mapping(snapshot.get("contract"), "portable PredictionSnapshot contract")
    if any(contract.get(key) != value for key, value in expected_contract.items()):
        raise ValueError("portable PredictionSnapshot contract drift")
    if "final_holdout" in str(contract.get("fold_id") or "").lower():
        raise ValueError("portable PredictionSnapshot accesses final holdout")
    return frame.sort_index()


def _verify_anchors(
    lock: Mapping[str, Any], payloads: Mapping[str, Path], references: Mapping[str, Any]
) -> None:
    lineage = _mapping(lock.get("lineage"), "Phase 3 lineage")
    release = _mapping(lineage.get("dataRelease"), "locked DataRelease")
    feature = _mapping(lineage.get("featureSnapshot"), "locked FeatureSnapshot")
    contract = _mapping(lock.get("contract"), "Phase 3 contract")
    frames: list[pd.DataFrame] = []
    snapshot_payloads = _mapping(
        references.get("predictionPayloads"), "PredictionSnapshot payload references"
    )
    for raw_anchor in _mapping(lineage.get("anchors"), "Phase 3 anchors").values():
        anchor = _mapping(raw_anchor, "Phase 3 anchor")
        for raw_run in _sequence(anchor.get("runs"), "Phase 3 anchor runs"):
            run = _mapping(raw_run, "Phase 3 anchor run")
            run_path = payloads.get(str(run.get("path") or ""))
            snapshot_ref = _mapping(run.get("predictionSnapshot"), "Phase 3 anchor PredictionSnapshot")
            snapshot_path = payloads.get(str(snapshot_ref.get("path") or ""))
            payload_path = payloads.get(str(snapshot_payloads.get(str(snapshot_ref.get("path") or "")) or ""))
            if run_path is None or snapshot_path is None or payload_path is None:
                raise ValueError("portable Phase 3 anchor inventory is incomplete")
            if sha256_file(run_path) != run.get("sha256") or sha256_file(snapshot_path) != snapshot_ref.get(
                "sha256"
            ):
                raise ValueError("portable Phase 3 anchor manifest checksum mismatch")
            run_manifest = _load_json(run_path, "portable Phase 2 run manifest")
            if (
                _contains_final_holdout(run_manifest)
                or _mapping(run_manifest.get("promotion"), "anchor promotion").get("promotionAuthorized")
                is not False
            ):
                raise ValueError("portable Phase 3 anchor violates holdout or promotion isolation")
            expected = {
                "data_release_id": release.get("dataReleaseId"),
                "feature_snapshot_id": feature.get("featureSnapshotId"),
                "label_spec_id": contract.get("label_spec"),
                "feature_set_id": anchor.get("featureSet"),
                "alpha_pack_id": "ashare_alpha_phase2_v1",
            }
            frame = _verify_prediction_snapshot(snapshot_path, payload_path, expected_contract=expected)
            snapshot = _load_json(snapshot_path, "portable PredictionSnapshot manifest")
            if run_manifest.get("predictionSnapshot") != snapshot or snapshot.get(
                "snapshotId"
            ) != snapshot_ref.get("snapshotId"):
                raise ValueError("portable Phase 3 anchor PredictionSnapshot binding drift")
            if snapshot["payload"].get("sha256") != snapshot_ref.get("payloadSha256"):
                raise ValueError("portable Phase 3 anchor payload binding drift")
            frames.append(frame)
    if not frames:
        raise ValueError("portable Phase 3 anchor inventory is empty")
    reference = frames[0]
    for frame in frames[1:]:
        if not frame.index.equals(reference.index) or not frame["label"].equals(reference["label"]):
            raise ValueError("portable Phase 3 anchor predictions or labels do not align")


def _verify_diagnosis(
    lock: Mapping[str, Any],
    lock_path: Path,
    plan: Mapping[str, Any],
    plan_path: Path,
    diagnosis_path: Path,
    *,
    references: Mapping[str, Any],
    payloads: Mapping[str, Path],
) -> None:
    manifest = _load_json(diagnosis_path, "portable Phase 3 evidence index")
    if manifest.get("evidenceSha256") != sha256_json(
        {key: value for key, value in manifest.items() if key != "evidenceSha256"}
    ):
        raise ValueError("portable Phase 3 evidence-index checksum mismatch")
    if (
        manifest.get("schemaVersion") != PHASE3_EVIDENCE_INDEX_SCHEMA
        or manifest.get("contractLockSha256") != lock.get("lockSha256")
        or manifest.get("programId") != lock.get("programId")
    ):
        raise ValueError("portable Phase 3 evidence index design-lock mismatch")
    contract_binding = _mapping(manifest.get("contractLock"), "portable diagnosis lock binding")
    plan_binding = _mapping(manifest.get("diagnosticPlan"), "portable diagnosis plan binding")
    if (
        contract_binding.get("sha256") != sha256_file(lock_path)
        or contract_binding.get("lockSha256") != lock.get("lockSha256")
        or plan_binding.get("sha256") != sha256_file(plan_path)
        or plan_binding.get("planSha256") != plan.get("planSha256")
    ):
        raise ValueError("portable Phase 3 diagnosis lock or plan binding mismatch")
    if (
        manifest.get("state") != "PHASE3_DIAGNOSIS_COMPLETE"
        or tuple(manifest.get("completedWorkstreams", ())) != PHASE3_EXECUTION_ORDER
        or manifest.get("diagnosisOnly") is not True
        or manifest.get("formalCandidates") != []
        or manifest.get("formalCandidateCount") != 0
        or manifest.get("confirmationState") != "NOT_STARTED"
        or manifest.get("finalHoldoutAccessed") is not False
        or manifest.get("selectionUsesFinalHoldout") is not False
        or manifest.get("publishingAuthorized") is not False
    ):
        raise ValueError("portable Phase 3 diagnosis isolation state drift")
    if manifest.get("phase2Evidence") != _mapping(lock.get("entryCondition"), "entry condition").get(
        "phase2Evidence"
    ):
        raise ValueError("portable Phase 3 diagnosis Phase 2 evidence binding mismatch")
    artifacts = [
        _mapping(raw, "portable Phase 3 artifact")
        for raw in _sequence(manifest.get("artifacts"), "Phase 3 artifacts")
    ]
    expected = _expected_artifact_names(lock)
    names = [str(item.get("name") or "") for item in artifacts]
    paths = [str(item.get("path") or "") for item in artifacts]
    if set(names) != expected or set(paths) != expected or len(names) != len(expected) or names != paths:
        raise ValueError("portable Phase 3 diagnosis artifact set is incomplete or unexpected")
    artifact_sources = _mapping(
        references.get("diagnosisArtifacts"), "portable diagnosis artifact references"
    )
    for artifact in artifacts:
        name = str(artifact["path"])
        target = payloads.get(str(artifact_sources.get(name) or ""))
        if target is None or sha256_file(target) != artifact.get("sha256"):
            raise ValueError("portable Phase 3 diagnosis artifact checksum mismatch")
        if target.suffix == ".parquet" and int(artifact.get("rows", -1)) != len(pd.read_parquet(target)):
            raise ValueError("portable Phase 3 diagnosis artifact row-count mismatch")
    anchor_index = _load_json(
        payloads[str(artifact_sources["anchor_predictions_index.json"])], "portable anchor predictions index"
    )
    if (
        anchor_index.get("schemaVersion") != PHASE3_DIAGNOSTICS_SCHEMA
        or anchor_index.get("anchors") != _mapping(lock.get("lineage"), "lineage").get("anchors")
        or anchor_index.get("finalHoldout") is not False
        or anchor_index.get("publishingAuthorized") is not False
    ):
        raise ValueError("portable Phase 3 anchor index state drift")
    summary = _load_json(
        payloads[str(artifact_sources["phase3_diagnostics_report.json"])], "portable Phase 3 summary"
    )
    if summary != manifest.get("summary"):
        raise ValueError("portable Phase 3 summary differs from evidence index")


def verify_phase3_portable_evidence(package_root: str | Path) -> dict[str, Any]:
    """Verify a package without training, diagnosis execution, or holdout access."""

    _, manifest, payloads = _load_portable(package_root)
    lock_path = _package_input(manifest, payloads, "designLock")
    plan_path = _package_input(manifest, payloads, "diagnosticPlan")
    lock = load_phase3_lock(lock_path)
    plan = load_phase3_plan(plan_path, contract_lock_sha256=str(lock["lockSha256"]))
    if manifest.get("contractLockSha256") != lock.get("lockSha256") or manifest.get("planSha256") != plan.get(
        "planSha256"
    ):
        raise ValueError("portable evidence package lock or plan identity mismatch")
    references = _mapping(manifest.get("references"), "portable evidence references")
    locked_entry = _mapping(lock.get("entryCondition"), "Phase 3 entry condition")
    for lock_key, input_name in (
        ("phase2Acceptance", "phase2Acceptance"),
        ("phase2CandidateMetrics", "phase2CandidateMetrics"),
        ("phase2Evidence", "phase2Evidence"),
        ("dataReleaseAcceptance", "dataReleaseAcceptance"),
    ):
        expected = _mapping(locked_entry.get(lock_key), lock_key).get("sha256")
        if sha256_file(_package_input(manifest, payloads, input_name)) != expected:
            raise ValueError(f"portable {lock_key} checksum differs from the design lock")

    revision = git_revision(Path(__file__).resolve().parents[3])
    lineage = _mapping(lock.get("lineage"), "Phase 3 lineage")
    if revision.get("commit") != lineage.get("sourceCodeCommit") or revision.get("dirty") is not False:
        raise ValueError(
            "portable verification requires the clean source-code commit frozen by the design lock"
        )
    implementation_root = Path(__file__).resolve().parent
    for name, expected in _mapping(
        lineage.get("implementationSha256"), "Phase 3 implementation hashes"
    ).items():
        target = implementation_root / str(name)
        if not target.is_file() or sha256_file(target) != expected:
            raise ValueError(f"portable verification implementation drift: {name}")

    contract = load_phase3_contract(_package_input(manifest, payloads, "phase3Contract"))
    locked_contract = _mapping(lock.get("contract"), "Phase 3 locked contract")
    if contract.file_sha256 != locked_contract.get(
        "file_sha256"
    ) or contract.semantic_sha256 != locked_contract.get("semantic_sha256"):
        raise ValueError("portable verification contract drift")
    regime = load_regime_spec(_package_input(manifest, payloads, "regimeSpec"))
    locked_regime = _mapping(lineage.get("regimeSpec"), "Phase 3 regime spec")
    if regime.file_sha256 != locked_regime.get("fileSha256") or regime.semantic_sha256 != locked_regime.get(
        "semanticSha256"
    ):
        raise ValueError("portable verification regime spec drift")

    acceptance_path = _package_input(manifest, payloads, "phase2Acceptance")
    evidence_path = _package_input(manifest, payloads, "phase2Evidence")
    collector_path = _package_input(manifest, payloads, "phase2CandidateMetrics")
    data_acceptance_path = _package_input(manifest, payloads, "dataReleaseAcceptance")
    acceptance = _validate_phase2_acceptance(acceptance_path, str(lock.get("predecessorProgram") or ""))
    evidence = _load_json(evidence_path, "portable Phase 2 evidence index")
    collector = _load_json(collector_path, "portable Phase 2 candidate metrics")
    if evidence.get("schemaVersion") != PHASE2_EVIDENCE_SCHEMA or evidence.get("finalHoldout") is not False:
        raise ValueError("portable Phase 2 evidence isolation state drift")
    if collector.get("collectorSha256") != sha256_json(
        {key: value for key, value in collector.items() if key != "collectorSha256"}
    ):
        raise ValueError("portable Phase 2 collector checksum mismatch")
    binding = _mapping(acceptance.get("candidateMetrics"), "Phase 2 candidate-metrics binding")
    if (
        sha256_file(collector_path) != binding.get("sha256")
        or collector.get("collectorSha256") != binding.get("collectorSha256")
        or _mapping(collector.get("evidenceIndex"), "collector evidence binding").get("sha256")
        != sha256_file(evidence_path)
    ):
        raise ValueError("portable Phase 2 acceptance/collector/evidence binding mismatch")
    collector_candidates = tuple(
        sorted(
            str(_mapping(raw, "collector candidate").get("candidateId") or "")
            for raw in _sequence(collector.get("candidates"), "collector candidates")
        )
    )
    if collector_candidates != PHASE2_INCREMENTAL_CANDIDATE_FAMILY:
        raise ValueError("portable Phase 2 collector candidate family drift")
    release_path = _package_input(manifest, payloads, "dataReleaseManifest")
    release = _verify_release(release_path, references=references, payloads=payloads)
    locked_release = _mapping(lineage.get("dataRelease"), "locked DataRelease")
    if release.get("dataReleaseId") != locked_release.get("dataReleaseId") or release.get(
        "manifestSha256"
    ) != locked_release.get("manifestSha256"):
        raise ValueError("portable DataRelease differs from Phase 3 design lock")
    _validate_data_release_acceptance(
        data_acceptance_path,
        data_release_id=str(release["dataReleaseId"]),
        manifest_sha256=str(release["manifestSha256"]),
    )
    feature = _verify_feature_snapshot(
        _package_input(manifest, payloads, "featureSnapshot"), references=references, payloads=payloads
    )
    locked_feature = _mapping(lineage.get("featureSnapshot"), "locked FeatureSnapshot")
    if feature.get("featureSnapshotId") != locked_feature.get("featureSnapshotId"):
        raise ValueError("portable FeatureSnapshot differs from Phase 3 design lock")
    labels_path = _package_input(manifest, payloads, "phase2Labels")
    if sha256_file(labels_path) != _mapping(lineage.get("labels"), "locked labels").get("sha256"):
        raise ValueError("portable labels differ from Phase 3 design lock")
    _verify_anchors(lock, payloads, references)
    _verify_diagnosis(
        lock,
        lock_path,
        plan,
        plan_path,
        _package_input(manifest, payloads, "diagnosisEvidenceIndex"),
        references=references,
        payloads=payloads,
    )
    return {
        "schemaVersion": PHASE3_PORTABLE_EVIDENCE_SCHEMA,
        "programId": lock["programId"],
        "contractLockSha256": lock["lockSha256"],
        "planSha256": plan["planSha256"],
        "state": "PHASE3_DIAGNOSIS_COMPLETE",
        "confirmationState": "NOT_STARTED",
        "finalHoldoutAccessed": False,
        "publishingAuthorized": False,
        "packageSha256": manifest["packageSha256"],
    }
