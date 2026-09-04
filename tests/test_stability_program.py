from __future__ import annotations

import json
from pathlib import Path

from qlib_platform.research.contracts.stability_program import write_stability_contract_lock
from qlib_platform.lineage import sha256_json
from qlib_platform.research.workflow.stability_program import (
    load_stability_plan,
    write_stability_experiment_plan,
)

from tests._stability_helpers import phase3_entry_fixture


def test_stability_plan_stops_at_d04_and_never_creates_candidates(tmp_path: Path):
    acceptance, evidence, data_acceptance = phase3_entry_fixture(tmp_path)
    lock = write_stability_contract_lock(
        candidate_acceptance=acceptance,
        phase2_evidence=evidence,
        candidate_data_acceptance=data_acceptance,
        contract_path="configs/research/ashare_stability_diagnostics_v1.yaml",
        output=tmp_path / "phase3-lock.json",
    )
    path = write_stability_experiment_plan(contract_lock=lock, output=tmp_path / "plan.json")
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["executionOrder"] == ["P3-D00", "P3-D01", "P3-D02", "P3-D03", "P3-D04"]
    assert all(item["requiresRetraining"] is False for item in payload["workstreams"])
    assert all(item["producesFormalCandidate"] is False for item in payload["workstreams"])
    assert payload["confirmationHypotheses"] == []
    assert payload["finalHoldoutAccessAllowed"] is False
    assert payload["publishingAuthorized"] is False


def test_stability_plan_is_immutable(tmp_path: Path):
    acceptance, evidence, data_acceptance = phase3_entry_fixture(tmp_path)
    lock = write_stability_contract_lock(
        candidate_acceptance=acceptance,
        phase2_evidence=evidence,
        candidate_data_acceptance=data_acceptance,
        contract_path="configs/research/ashare_stability_diagnostics_v1.yaml",
        output=tmp_path / "phase3-lock.json",
    )
    output = tmp_path / "plan.json"
    write_stability_experiment_plan(contract_lock=lock, output=output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    payload["publishingAuthorized"] = True
    output.write_text(json.dumps(payload), encoding="utf-8")

    try:
        write_stability_experiment_plan(contract_lock=lock, output=output)
    except ValueError as exc:
        assert "differs" in str(exc)
    else:
        raise AssertionError("tampered Phase 3 plan was accepted")


def test_stability_plan_rejects_design_lock_rebinding(tmp_path: Path):
    acceptance, evidence, data_acceptance = phase3_entry_fixture(tmp_path)
    lock = write_stability_contract_lock(
        candidate_acceptance=acceptance,
        phase2_evidence=evidence,
        candidate_data_acceptance=data_acceptance,
        contract_path="configs/research/ashare_stability_diagnostics_v1.yaml",
        output=tmp_path / "phase3-lock.json",
    )
    lock_payload = json.loads(lock.read_text(encoding="utf-8"))
    plan = write_stability_experiment_plan(contract_lock=lock, output=tmp_path / "plan.json")
    payload = json.loads(plan.read_text(encoding="utf-8"))
    payload["contractLock"]["lockSha256"] = "different-lock"
    payload["planSha256"] = sha256_json({key: value for key, value in payload.items() if key != "planSha256"})
    plan.write_text(json.dumps(payload), encoding="utf-8")

    try:
        load_stability_plan(plan, contract_lock_sha256=lock_payload["lockSha256"])
    except ValueError as exc:
        assert "different design lock" in str(exc)
    else:
        raise AssertionError("rebound Phase 3 plan was accepted")
