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
)
from qlib_platform.research.diagnostics.portability import (
    export_stability_portable_evidence,
    verify_stability_portable_evidence,
)
from qlib_platform.research.workflow.stability_program import write_stability_experiment_plan
from qlib_platform.data.store import sha256_file

from tests._stability_helpers import phase3_entry_fixture


def _diagnosis_bundle(tmp_path: Path) -> tuple[Path, Path, Path]:
    acceptance, evidence, data_acceptance = phase3_entry_fixture(tmp_path / "entry")
    lock_path = write_stability_contract_lock(
        candidate_acceptance=acceptance,
        candidate_evidence=evidence,
        candidate_data_acceptance=data_acceptance,
        contract_path="configs/research/ashare_stability_diagnostics_v1.yaml",
        output=tmp_path / "phase3-lock.json",
    )
    plan_path = write_stability_experiment_plan(
        contract_lock=lock_path, output=tmp_path / "stability-plan.json"
    )
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    output = tmp_path / "diagnosis"
    output.mkdir()
    artifacts: list[dict[str, object]] = []
    for name in sorted(_expected_artifact_names(lock)):
        target = output / name
        if target.suffix == ".parquet":
            pd.DataFrame({"value": [1]}).to_parquet(target, index=False)
            artifacts.append({"name": name, "path": name, "sha256": sha256_file(target), "rows": 1})
        else:
            value: object = {}
            if name == "anchor_predictions_index.json":
                value = {
                    "schemaVersion": "phase3_diagnostics_v1",
                    "anchors": lock["lineage"]["anchors"],
                    "foldCalendar": {},
                    "finalHoldout": False,
                    "publishingAuthorized": False,
                }
            target.write_text(json.dumps(value) if target.suffix == ".json" else str(value), encoding="utf-8")
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
    (output / STABILITY_MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    return output, lock_path, plan_path


def test_portable_phase3_evidence_rejects_tampering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    diagnosis, lock_path, plan_path = _diagnosis_bundle(tmp_path)
    package_root = tmp_path.parent / "portable-package"
    manifest_path = export_stability_portable_evidence(
        contract_lock=lock_path,
        plan_path=plan_path,
        diagnosis=diagnosis,
        contract_path="configs/research/ashare_stability_diagnostics_v1.yaml",
        data_root=tmp_path / "entry",
        output=package_root,
    )
    package = json.loads(manifest_path.read_text(encoding="utf-8"))
    monkeypatch.setattr(
        "qlib_platform.research.diagnostics.portability.git_revision",
        lambda _: {"commit": package["sourceCodeCommit"], "dirty": False},
    )
    verified = verify_stability_portable_evidence(package_root)
    assert verified["state"] == "PHASE3_DIAGNOSIS_COMPLETE"
    payload = next((package_root / "payload").iterdir())
    payload.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="checksum or size mismatch"):
        verify_stability_portable_evidence(package_root)


def test_portable_phase3_evidence_rejects_unexpected_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    diagnosis, lock_path, plan_path = _diagnosis_bundle(tmp_path)
    package_root = tmp_path.parent / "portable-package-extra"
    manifest_path = export_stability_portable_evidence(
        contract_lock=lock_path,
        plan_path=plan_path,
        diagnosis=diagnosis,
        contract_path="configs/research/ashare_stability_diagnostics_v1.yaml",
        data_root=tmp_path / "entry",
        output=package_root,
    )
    package = json.loads(manifest_path.read_text(encoding="utf-8"))
    monkeypatch.setattr(
        "qlib_platform.research.diagnostics.portability.git_revision",
        lambda _: {"commit": package["sourceCodeCommit"], "dirty": False},
    )
    (package_root / "unexpected.txt").write_text("not part of the package", encoding="utf-8")
    with pytest.raises(ValueError, match="missing or unexpected files"):
        verify_stability_portable_evidence(package_root)
