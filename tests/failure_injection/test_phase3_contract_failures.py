from __future__ import annotations

import json
from pathlib import Path

import pytest

from qlib_platform.lineage import sha256_json
from qlib_platform.research.phase3_contract import write_phase3_contract_lock
from qlib_platform.data.store import sha256_file

from tests._phase3_helpers import phase3_entry_fixture


def _write_lock(tmp_path: Path, acceptance: Path, evidence: Path, data_acceptance: Path) -> None:
    write_phase3_contract_lock(
        phase2_acceptance=acceptance,
        phase2_evidence=evidence,
        phase2_data_acceptance=data_acceptance,
        contract_path="configs/research/ashare_phase3_v1.yaml",
        output=tmp_path / "phase3-lock.json",
    )


def _reseal_phase2_provenance(acceptance_path: Path, evidence_path: Path) -> None:
    acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
    metrics_path = Path(acceptance["candidateMetrics"]["path"])
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    evidence_sha = sha256_file(evidence_path)
    metrics["evidenceIndex"]["sha256"] = evidence_sha
    metrics["collectorSha256"] = sha256_json(
        {key: value for key, value in metrics.items() if key != "collectorSha256"}
    )
    metrics_path.write_text(json.dumps(metrics), encoding="utf-8")
    acceptance["candidateMetrics"].update(
        {
            "sha256": sha256_file(metrics_path),
            "collectorSha256": metrics["collectorSha256"],
        }
    )
    acceptance["candidateMetrics"]["evidenceIndex"]["sha256"] = evidence_sha
    acceptance["acceptanceSha256"] = sha256_json(
        {key: value for key, value in acceptance.items() if key != "acceptanceSha256"}
    )
    acceptance_path.write_text(json.dumps(acceptance), encoding="utf-8")


def test_phase3_rejects_rewriting_phase2_rejection_as_acceptance(tmp_path: Path):
    acceptance_path, evidence_path, data_acceptance_path = phase3_entry_fixture(tmp_path)
    acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
    acceptance["acceptedCount"] = 1
    acceptance["candidates"][0]["gatePass"] = True
    acceptance["candidates"][0]["status"] = "RESEARCH_CANDIDATE"
    acceptance["acceptanceSha256"] = sha256_json(
        {key: value for key, value in acceptance.items() if key != "acceptanceSha256"}
    )
    acceptance_path.write_text(json.dumps(acceptance), encoding="utf-8")

    with pytest.raises(ValueError, match="acceptedCount=0"):
        _write_lock(tmp_path, acceptance_path, evidence_path, data_acceptance_path)


def test_phase3_rejects_final_holdout_contamination(tmp_path: Path):
    acceptance_path, evidence_path, data_acceptance_path = phase3_entry_fixture(tmp_path)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    run_path = Path(evidence["ablationExperiments"]["P2-06"]["runManifests"][0])
    run = json.loads(run_path.read_text(encoding="utf-8"))
    run["folds"][0]["final_holdout"] = True
    run_path.write_text(json.dumps(run), encoding="utf-8")

    with pytest.raises(ValueError, match="final-holdout"):
        _write_lock(tmp_path, acceptance_path, evidence_path, data_acceptance_path)


def test_phase3_rejects_wrong_anchor_model_profile(tmp_path: Path):
    acceptance_path, evidence_path, data_acceptance_path = phase3_entry_fixture(tmp_path)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    run_path = Path(evidence["ablationExperiments"]["P2-08"]["runManifests"][0])
    run = json.loads(run_path.read_text(encoding="utf-8"))
    run["runtime"]["modelFamily"] = "ridge"
    run_path.write_text(json.dumps(run), encoding="utf-8")

    with pytest.raises(ValueError, match="feature/model binding drift"):
        _write_lock(tmp_path, acceptance_path, evidence_path, data_acceptance_path)


def test_phase3_rejects_dirty_anchor_source_lineage(tmp_path: Path):
    acceptance_path, evidence_path, data_acceptance_path = phase3_entry_fixture(tmp_path)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    run_path = Path(evidence["ablationExperiments"]["P2-07"]["runManifests"][0])
    run = json.loads(run_path.read_text(encoding="utf-8"))
    run["lineage"]["qlibPlatformDirty"] = True
    run_path.write_text(json.dumps(run), encoding="utf-8")

    with pytest.raises(ValueError, match="clean complete source-code lineage"):
        _write_lock(tmp_path, acceptance_path, evidence_path, data_acceptance_path)


def test_phase3_rejects_evidence_contract_drift(tmp_path: Path):
    acceptance_path, evidence_path, data_acceptance_path = phase3_entry_fixture(tmp_path)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["contractLockSha256"] = "different-lock"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    with pytest.raises(ValueError, match="collector is not bound"):
        _write_lock(tmp_path, acceptance_path, evidence_path, data_acceptance_path)


def test_phase3_rejects_truncated_phase2_candidate_family(tmp_path: Path):
    acceptance_path, evidence_path, data_acceptance_path = phase3_entry_fixture(tmp_path)
    acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
    acceptance["candidates"].pop()
    acceptance["acceptanceSha256"] = sha256_json(
        {key: value for key, value in acceptance.items() if key != "acceptanceSha256"}
    )
    acceptance_path.write_text(json.dumps(acceptance), encoding="utf-8")

    with pytest.raises(ValueError, match="exactly the frozen Phase 2 candidate family"):
        _write_lock(tmp_path, acceptance_path, evidence_path, data_acceptance_path)


def test_phase3_rejects_acceptance_that_differs_from_bound_collector(tmp_path: Path):
    acceptance_path, evidence_path, data_acceptance_path = phase3_entry_fixture(tmp_path)
    acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
    metrics_path = Path(acceptance["candidateMetrics"]["path"])
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics["candidates"][0]["metrics"] = {"tampered": 1}
    metrics["collectorSha256"] = sha256_json(
        {key: value for key, value in metrics.items() if key != "collectorSha256"}
    )
    metrics_path.write_text(json.dumps(metrics), encoding="utf-8")
    acceptance["candidateMetrics"]["sha256"] = sha256_file(metrics_path)
    acceptance["candidateMetrics"]["collectorSha256"] = metrics["collectorSha256"]
    acceptance["acceptanceSha256"] = sha256_json(
        {key: value for key, value in acceptance.items() if key != "acceptanceSha256"}
    )
    acceptance_path.write_text(json.dumps(acceptance), encoding="utf-8")

    with pytest.raises(ValueError, match="differs from collector"):
        _write_lock(tmp_path, acceptance_path, evidence_path, data_acceptance_path)


def test_phase3_rejects_failed_data_release_acceptance(tmp_path: Path):
    acceptance_path, evidence_path, data_acceptance_path = phase3_entry_fixture(tmp_path)
    data_acceptance = json.loads(data_acceptance_path.read_text(encoding="utf-8"))
    data_acceptance["checks"]["PIT_LEAKAGE"]["status"] = "FAIL"
    data_acceptance["acceptanceSha256"] = sha256_json(
        {key: value for key, value in data_acceptance.items() if key != "acceptanceSha256"}
    )
    data_acceptance_path.write_text(json.dumps(data_acceptance), encoding="utf-8")

    with pytest.raises(ValueError, match="non-PASS"):
        _write_lock(tmp_path, acceptance_path, evidence_path, data_acceptance_path)


def test_phase3_rejects_optional_anchor_portfolio_evidence(tmp_path: Path):
    acceptance_path, evidence_path, data_acceptance_path = phase3_entry_fixture(tmp_path)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["ablationExperiments"]["P2-06"]["portfolioManifest"] = "unbound-portfolio.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    _reseal_phase2_provenance(acceptance_path, evidence_path)

    with pytest.raises(ValueError, match="prohibits unbound optional portfolio evidence"):
        _write_lock(tmp_path, acceptance_path, evidence_path, data_acceptance_path)
