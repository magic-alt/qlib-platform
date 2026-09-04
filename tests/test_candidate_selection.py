from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from qlib_platform.research.contracts.candidate_program import write_candidate_contract_lock
from qlib_platform.research.evaluation.selection import open_final_holdout, write_candidate_selection_lock


def _json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    return path


def _phase1(path: Path) -> Path:
    return _json(
        path,
        {
            "schemaVersion": "alpha_phase1_synthesis_v1",
            "studyId": "aps_test",
            "status": {"phase1Completion": "COMPLETE"},
            "primaryRecommendation": "ALPHA_PACK_V2",
            "selectionUsesFinalHoldout": False,
            "publishingAuthorized": False,
        },
    )


def _release(path: Path, release_id: str, *, end: str, parent: str | None = None) -> Path:
    return _json(
        path,
        {
            "schemaVersion": "2.0",
            "dataReleaseId": release_id,
            "manifestSha256": release_id.removeprefix("ds_")[:64].ljust(64, "0"),
            "profile": "ashare_qlib_research_v2",
            "coverage": {"start": "2016-02-01", "end": end},
            "policies": {"pit": "next_open_after_final_announcement"},
            "lineage": {"parentDataReleaseId": parent} if parent else {},
        },
    )


def _candidate(candidate_id: str = "candidate-1") -> dict[str, object]:
    return {
        "candidateId": candidate_id,
        "status": "RESEARCH_CANDIDATE",
        "gatePass": True,
        "alphaPack": "ashare_alpha_phase2_v1",
        "featureSet": "A7",
        "model": "ridge",
        "portfolio": "topk_dropout_v1",
        "regimeRule": "none",
    }


def _contract_lock(tmp_path: Path) -> Path:
    return write_candidate_contract_lock(
        phase1_manifest=_phase1(tmp_path / "phase1.json"),
        contract_path="configs/research/ashare_candidate_research_v1.yaml",
        output=tmp_path / "contract-lock.json",
    )


def test_selection_lock_accepts_only_one_to_three_gate_passing_candidates(tmp_path: Path):
    design = _release(tmp_path / "design.json", "ds_design", end="2026-08-10")
    with pytest.raises(ValueError, match="one to three"):
        write_candidate_selection_lock(
            contract_lock=_contract_lock(tmp_path),
            candidates=[],
            design_release_manifest=design,
            selection_date="2026-08-16",
            output=tmp_path / "selection.json",
        )
    lock = write_candidate_selection_lock(
        contract_lock=_contract_lock(tmp_path),
        candidates=[_candidate()],
        design_release_manifest=design,
        selection_date="2026-08-16",
        output=tmp_path / "selection.json",
    )
    payload = json.loads(lock.read_text(encoding="utf-8"))
    assert payload["finalHoldout"]["status"] == "SEALED"
    assert payload["finalHoldout"]["sessions"] == 252
    assert payload["publishingAuthorized"] is False


def test_final_holdout_requires_append_only_release_maturity_and_single_access(tmp_path: Path):
    design_id = "ds_design"
    design = _release(tmp_path / "design.json", design_id, end="2026-08-10")
    selection = write_candidate_selection_lock(
        contract_lock=_contract_lock(tmp_path),
        candidates=[_candidate()],
        design_release_manifest=design,
        selection_date="2026-08-16",
        output=tmp_path / "selection.json",
    )
    calendar = pd.bdate_range("2026-08-17", periods=270)
    final = _release(
        tmp_path / "final.json",
        "ds_final",
        end=str(calendar[-1].date()),
        parent=design_id,
    )
    receipt = open_final_holdout(
        selection_lock=selection,
        final_release_manifest=final,
        trading_calendar=calendar,
        output=tmp_path / "holdout-open.json",
    )
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["window"]["sessions"] == 252
    assert payload["accessOrdinal"] == 1
    with pytest.raises(PermissionError, match="already been opened"):
        open_final_holdout(
            selection_lock=selection,
            final_release_manifest=final,
            trading_calendar=calendar,
            output=receipt,
        )


def test_final_holdout_rejects_unrelated_release(tmp_path: Path):
    design = _release(tmp_path / "design.json", "ds_design", end="2026-08-10")
    selection = write_candidate_selection_lock(
        contract_lock=_contract_lock(tmp_path),
        candidates=[_candidate()],
        design_release_manifest=design,
        selection_date="2026-08-16",
        output=tmp_path / "selection.json",
    )
    final = _release(tmp_path / "final.json", "ds_final", end="2028-01-01", parent="ds_other")
    with pytest.raises(ValueError, match="append-only"):
        open_final_holdout(
            selection_lock=selection,
            final_release_manifest=final,
            trading_calendar=pd.bdate_range("2026-08-17", periods=270),
            output=tmp_path / "receipt.json",
        )
