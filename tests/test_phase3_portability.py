from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from tushare_qlib.lineage import sha256_json
from tushare_qlib.research.phase3_contract import write_phase3_contract_lock
from tushare_qlib.research.phase3_diagnostics import (
    PHASE3_EVIDENCE_INDEX_SCHEMA,
    PHASE3_MANIFEST_NAME,
    _expected_artifact_names,
)
from tushare_qlib.research.phase3_portability import (
    export_phase3_portable_evidence,
    verify_phase3_portable_evidence,
)
from tushare_qlib.research.phase3_program import write_phase3_experiment_plan
from tushare_qlib.store import sha256_file

from tests._phase3_helpers import phase3_entry_fixture


def _diagnosis_bundle(tmp_path: Path) -> tuple[Path, Path, Path]:
    acceptance, evidence, data_acceptance = phase3_entry_fixture(tmp_path / "entry")
    lock_path = write_phase3_contract_lock(
        phase2_acceptance=acceptance,
        phase2_evidence=evidence,
        phase2_data_acceptance=data_acceptance,
        contract_path="configs/research/ashare_phase3_v1.yaml",
        output=tmp_path / "phase3-lock.json",
    )
    plan_path = write_phase3_experiment_plan(contract_lock=lock_path, output=tmp_path / "phase3-plan.json")
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
        "schemaVersion": PHASE3_EVIDENCE_INDEX_SCHEMA,
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
    (output / PHASE3_MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    return output, lock_path, plan_path


def test_portable_phase3_evidence_rejects_tampering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    diagnosis, lock_path, plan_path = _diagnosis_bundle(tmp_path)
    package_root = tmp_path.parent / "portable-package"
    manifest_path = export_phase3_portable_evidence(
        contract_lock=lock_path,
        plan_path=plan_path,
        diagnosis=diagnosis,
        contract_path="configs/research/ashare_phase3_v1.yaml",
        data_root=tmp_path / "entry",
        output=package_root,
    )
    package = json.loads(manifest_path.read_text(encoding="utf-8"))
    monkeypatch.setattr(
        "tushare_qlib.research.phase3_portability.git_revision",
        lambda _: {"commit": package["sourceCodeCommit"], "dirty": False},
    )
    verified = verify_phase3_portable_evidence(package_root)
    assert verified["state"] == "PHASE3_DIAGNOSIS_COMPLETE"
    payload = next((package_root / "payload").iterdir())
    payload.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="checksum or size mismatch"):
        verify_phase3_portable_evidence(package_root)


def test_portable_phase3_evidence_rejects_unexpected_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    diagnosis, lock_path, plan_path = _diagnosis_bundle(tmp_path)
    package_root = tmp_path.parent / "portable-package-extra"
    manifest_path = export_phase3_portable_evidence(
        contract_lock=lock_path,
        plan_path=plan_path,
        diagnosis=diagnosis,
        contract_path="configs/research/ashare_phase3_v1.yaml",
        data_root=tmp_path / "entry",
        output=package_root,
    )
    package = json.loads(manifest_path.read_text(encoding="utf-8"))
    monkeypatch.setattr(
        "tushare_qlib.research.phase3_portability.git_revision",
        lambda _: {"commit": package["sourceCodeCommit"], "dirty": False},
    )
    (package_root / "unexpected.txt").write_text("not part of the package", encoding="utf-8")
    with pytest.raises(ValueError, match="missing or unexpected files"):
        verify_phase3_portable_evidence(package_root)
