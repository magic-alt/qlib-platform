from __future__ import annotations

import json
from pathlib import Path

import pytest

from qlib_platform.cli import parser
from qlib_platform.research.contracts.candidate_program import (
    RECOMMENDATION_ROUTES,
    assert_workstream_allowed,
    load_candidate_contract,
    write_candidate_contract_lock,
)


def _phase1(path: Path, recommendation: str = "ALPHA_PACK_V2") -> Path:
    evidence = (
        {"modelExplanation": {"boundedSensitivity": "RECOVERABLE"}}
        if recommendation == "XGBOOST_TUNING"
        else {}
    )
    path.write_text(
        json.dumps(
            {
                "schemaVersion": "alpha_phase1_synthesis_v1",
                "studyId": "aps_test",
                "status": {"phase1Completion": "COMPLETE_WITH_KNOWN_DATA_GAP"},
                "primaryRecommendation": recommendation,
                "selectionUsesFinalHoldout": False,
                "publishingAuthorized": False,
                "evidence": evidence,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def test_repository_phase2_contract_freezes_research_protocol():
    contract = load_candidate_contract("configs/research/ashare_candidate_research_v1.yaml")

    assert contract.data_release_profile == "ashare_qlib_research_v2"
    assert contract.multiple_testing.romano_wolf_resamples == 5000
    assert contract.robustness.minimum_oriented_rank_ic == pytest.approx(0.01)
    assert contract.holdout.sessions == 252
    assert contract.holdout.access_limit == 1
    assert {item.hypothesis_id for item in contract.hypotheses} >= {"H001", "H106"}


@pytest.mark.parametrize("recommendation", sorted(RECOMMENDATION_ROUTES))
def test_phase1_recommendation_strictly_routes_workstreams(tmp_path: Path, recommendation: str):
    lock_path = write_candidate_contract_lock(
        synthesis_manifest=_phase1(tmp_path / "phase1.json", recommendation),
        contract_path="configs/research/ashare_candidate_research_v1.yaml",
        output=tmp_path / "phase2-lock.json",
    )
    lock = json.loads(lock_path.read_text(encoding="utf-8"))

    assert tuple(lock["recommendationRoute"]["allowedWorkstreams"]) == RECOMMENDATION_ROUTES[recommendation]
    assert lock["researchWindow"]["mode"] == "ROLLING_OOS_ONLY"
    assert lock["researchWindow"]["finalHoldoutArtifactsAllowed"] is False
    assert lock["publishingAuthorized"] is False


def test_workstream_gate_fails_closed(tmp_path: Path):
    lock_path = write_candidate_contract_lock(
        synthesis_manifest=_phase1(tmp_path / "phase1.json", "NO_GO_NEW_ALPHA"),
        contract_path="configs/research/ashare_candidate_research_v1.yaml",
        output=tmp_path / "phase2-lock.json",
    )
    lock = json.loads(lock_path.read_text(encoding="utf-8"))

    assert_workstream_allowed(lock, "BLOCKED_NO_GO_REPORT")
    with pytest.raises(PermissionError, match="does not authorize"):
        assert_workstream_allowed(lock, "ALPHA_CANDIDATES")


def test_phase2_rejects_incomplete_or_holdout_using_phase1(tmp_path: Path):
    source = _phase1(tmp_path / "phase1.json")
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["selectionUsesFinalHoldout"] = True
    source.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="isolation"):
        write_candidate_contract_lock(
            synthesis_manifest=source,
            contract_path="configs/research/ashare_candidate_research_v1.yaml",
            output=tmp_path / "phase2-lock.json",
        )


def test_phase2_contract_lock_is_immutable(tmp_path: Path):
    kwargs = {
        "synthesis_manifest": _phase1(tmp_path / "phase1.json"),
        "contract_path": "configs/research/ashare_candidate_research_v1.yaml",
        "output": tmp_path / "phase2-lock.json",
    }
    first = write_candidate_contract_lock(**kwargs)
    assert write_candidate_contract_lock(**kwargs) == first
    payload = json.loads(first.read_text(encoding="utf-8"))
    payload["programId"] = "tampered"
    first.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="differs"):
        write_candidate_contract_lock(**kwargs)


def test_phase2_cli_requires_explicit_phase1_manifest_and_output():
    args = parser().parse_args(
        [
            "candidate-validate",
            "--synthesis-manifest",
            "phase1.json",
            "--output",
            "phase2-lock.json",
        ]
    )
    assert args.command == "candidate-validate"
    assert args.contract == "configs/research/ashare_candidate_research_v1.yaml"
