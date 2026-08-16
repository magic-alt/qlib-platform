from __future__ import annotations

import json
from pathlib import Path

import pytest

from tushare_qlib.lineage import sha256_json
from tushare_qlib.research.phase3_contract import write_phase3_contract_lock

from tests._phase3_helpers import phase3_entry_fixture


def _write_lock(tmp_path: Path, acceptance: Path, evidence: Path) -> None:
    write_phase3_contract_lock(
        phase2_acceptance=acceptance,
        phase2_evidence=evidence,
        contract_path="configs/research/ashare_phase3_v1.yaml",
        output=tmp_path / "phase3-lock.json",
    )


def test_phase3_rejects_rewriting_phase2_rejection_as_acceptance(tmp_path: Path):
    acceptance_path, evidence_path = phase3_entry_fixture(tmp_path)
    acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
    acceptance["acceptedCount"] = 1
    acceptance["candidates"][0]["gatePass"] = True
    acceptance["candidates"][0]["status"] = "RESEARCH_CANDIDATE"
    acceptance["acceptanceSha256"] = sha256_json(
        {key: value for key, value in acceptance.items() if key != "acceptanceSha256"}
    )
    acceptance_path.write_text(json.dumps(acceptance), encoding="utf-8")

    with pytest.raises(ValueError, match="acceptedCount=0"):
        _write_lock(tmp_path, acceptance_path, evidence_path)


def test_phase3_rejects_final_holdout_contamination(tmp_path: Path):
    acceptance_path, evidence_path = phase3_entry_fixture(tmp_path)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    run_path = Path(evidence["ablationExperiments"]["P2-06"]["runManifests"][0])
    run = json.loads(run_path.read_text(encoding="utf-8"))
    run["folds"][0]["final_holdout"] = True
    run_path.write_text(json.dumps(run), encoding="utf-8")

    with pytest.raises(ValueError, match="final-holdout"):
        _write_lock(tmp_path, acceptance_path, evidence_path)


def test_phase3_rejects_wrong_anchor_model_profile(tmp_path: Path):
    acceptance_path, evidence_path = phase3_entry_fixture(tmp_path)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    run_path = Path(evidence["ablationExperiments"]["P2-08"]["runManifests"][0])
    run = json.loads(run_path.read_text(encoding="utf-8"))
    run["runtime"]["modelFamily"] = "ridge"
    run_path.write_text(json.dumps(run), encoding="utf-8")

    with pytest.raises(ValueError, match="feature/model binding drift"):
        _write_lock(tmp_path, acceptance_path, evidence_path)


def test_phase3_rejects_dirty_anchor_source_lineage(tmp_path: Path):
    acceptance_path, evidence_path = phase3_entry_fixture(tmp_path)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    run_path = Path(evidence["ablationExperiments"]["P2-07"]["runManifests"][0])
    run = json.loads(run_path.read_text(encoding="utf-8"))
    run["lineage"]["qlibPlatformDirty"] = True
    run_path.write_text(json.dumps(run), encoding="utf-8")

    with pytest.raises(ValueError, match="clean complete source-code lineage"):
        _write_lock(tmp_path, acceptance_path, evidence_path)


def test_phase3_rejects_evidence_contract_drift(tmp_path: Path):
    acceptance_path, evidence_path = phase3_entry_fixture(tmp_path)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["contractLockSha256"] = "different-lock"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    with pytest.raises(ValueError, match="acceptance/evidence contract lock mismatch"):
        _write_lock(tmp_path, acceptance_path, evidence_path)
