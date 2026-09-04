from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from qlib_platform.lineage import sha256_json
from qlib_platform.research.contracts.stability_program import write_stability_contract_lock
from qlib_platform.research.diagnostics.stability import (
    STABILITY_EVIDENCE_INDEX_SCHEMA,
    STABILITY_MANIFEST_NAME,
    _expected_artifact_names,
    _validate_existing,
)
from qlib_platform.research.workflow.stability_program import write_stability_experiment_plan
from qlib_platform.data.store import sha256_file

from tests._stability_helpers import phase3_entry_fixture


def _existing_bundle(tmp_path: Path) -> tuple[Path, Path, dict[str, object], Path, dict[str, object]]:
    acceptance, evidence, data_acceptance = phase3_entry_fixture(tmp_path / "entry")
    lock_path = write_stability_contract_lock(
        candidate_acceptance=acceptance,
        phase2_evidence=evidence,
        candidate_data_acceptance=data_acceptance,
        contract_path="configs/research/ashare_stability_diagnostics_v1.yaml",
        output=tmp_path / "phase3-lock.json",
    )
    plan_path = write_stability_experiment_plan(contract_lock=lock_path, output=tmp_path / "stability-plan.json")
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    output = (tmp_path / "diagnosis").resolve()
    output.mkdir()
    artifacts: list[dict[str, object]] = []
    for name in sorted(_expected_artifact_names(lock)):
        target = output / name
        if target.suffix == ".parquet":
            pd.DataFrame({"value": [1]}).to_parquet(target, index=False)
            artifacts.append({"name": name, "path": name, "sha256": sha256_file(target), "rows": 1})
        else:
            content: object = {}
            if name == "anchor_predictions_index.json":
                content = {
                    "schemaVersion": "phase3_diagnostics_v1",
                    "anchors": lock["lineage"]["anchors"],
                    "foldCalendar": {},
                    "finalHoldout": False,
                    "publishingAuthorized": False,
                }
            target.write_text(
                json.dumps(content) if target.suffix == ".json" else str(content), encoding="utf-8"
            )
            artifacts.append({"name": name, "path": name, "sha256": sha256_file(target)})
    manifest: dict[str, object] = {
        "schemaVersion": STABILITY_EVIDENCE_INDEX_SCHEMA,
        "programId": lock["programId"],
        "studyType": "ALPHA_STABILITY_REGIME_RESEARCH_DIAGNOSIS_ONLY",
        "contractLock": {
            "path": str(lock_path),
            "sha256": sha256_file(lock_path),
            "lockSha256": lock["lockSha256"],
        },
        "contractLockSha256": lock["lockSha256"],
        "diagnosticPlan": {
            "path": str(plan_path),
            "sha256": sha256_file(plan_path),
            "planSha256": plan["planSha256"],
        },
        "phase2Evidence": lock["entryCondition"]["phase2Evidence"],
        "lineage": {
            "dataReleaseId": lock["lineage"]["dataRelease"]["dataReleaseId"],
            "dataReleaseManifestSha256": lock["lineage"]["dataRelease"]["manifestSha256"],
            "datasetVersionId": lock["lineage"]["datasetVersionId"],
            "featureSnapshotId": lock["lineage"]["featureSnapshot"]["featureSnapshotId"],
            "regimeSemanticSha256": lock["lineage"]["regimeSpec"]["semanticSha256"],
            "sourceCodeCommit": lock["lineage"]["sourceCodeCommit"],
            "sourceCodeDirty": False,
        },
        "state": "PHASE3_DIAGNOSIS_COMPLETE",
        "completedWorkstreams": ["P3-D00", "P3-D01", "P3-D02", "P3-D03", "P3-D04"],
        "diagnosisOnly": True,
        "formalCandidates": [],
        "formalCandidateCount": 0,
        "confirmationState": "NOT_STARTED",
        "finalHoldoutAccessed": False,
        "selectionUsesFinalHoldout": False,
        "publishingAuthorized": False,
        "summary": {},
        "artifacts": artifacts,
    }
    manifest["evidenceSha256"] = sha256_json(manifest)
    manifest_path = output / STABILITY_MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return output, lock_path, lock, plan_path, plan


def _validate(
    output: Path,
    lock_path: Path,
    lock: dict[str, object],
    plan_path: Path,
    plan: dict[str, object],
) -> Path:
    return _validate_existing(
        output,
        lock=lock,
        lock_path=lock_path,
        plan=plan,
        plan_path=plan_path,
    )


def test_existing_phase3_bundle_requires_complete_artifact_set(tmp_path: Path):
    output, lock_path, lock, plan_path, plan = _existing_bundle(tmp_path)
    assert _validate(output, lock_path, lock, plan_path, plan) == output / STABILITY_MANIFEST_NAME

    manifest_path = output / STABILITY_MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"].pop()
    manifest["evidenceSha256"] = sha256_json(
        {key: value for key, value in manifest.items() if key != "evidenceSha256"}
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="artifact set is incomplete"):
        _validate(output, lock_path, lock, plan_path, plan)


def test_existing_phase3_bundle_rechecks_isolation_and_evidence_hash(tmp_path: Path):
    output, lock_path, lock, plan_path, plan = _existing_bundle(tmp_path)
    manifest_path = output / STABILITY_MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["publishingAuthorized"] = True
    manifest["evidenceSha256"] = sha256_json(
        {key: value for key, value in manifest.items() if key != "evidenceSha256"}
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="isolation state drift"):
        _validate(output, lock_path, lock, plan_path, plan)

    manifest["publishingAuthorized"] = False
    manifest["evidenceSha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="evidence-index checksum mismatch"):
        _validate(output, lock_path, lock, plan_path, plan)
