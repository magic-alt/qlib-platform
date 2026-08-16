from __future__ import annotations

from pathlib import Path

import pytest

from tushare_qlib.cli import parser
from tushare_qlib.research.phase3_contract import (
    load_phase3_contract,
    load_phase3_lock,
    write_phase3_contract_lock,
)

from tests._phase3_helpers import phase3_entry_fixture


def test_repository_phase3_contract_freezes_diagnosis_only_program():
    contract = load_phase3_contract("configs/research/ashare_phase3_v1.yaml")

    assert contract.program_id == "ashare_alpha_stability_phase3_v1"
    assert contract.objective == "TEMPORAL_ALPHA_STABILITY"
    assert [anchor.experiment_id for anchor in contract.anchors] == ["P2-06", "P2-07", "P2-08"]
    assert contract.diagnostics.rolling_windows == (63, 126, 252)
    assert contract.diagnostics.transition_windows == (20, 63)


def test_phase3_lock_binds_rejected_phase2_and_anchor_lineage(tmp_path: Path):
    acceptance, evidence = phase3_entry_fixture(tmp_path)
    path = write_phase3_contract_lock(
        phase2_acceptance=acceptance,
        phase2_evidence=evidence,
        contract_path="configs/research/ashare_phase3_v1.yaml",
        output=tmp_path / "phase3-lock.json",
    )
    lock = load_phase3_lock(path)

    assert lock["entryCondition"]["phase2AcceptedCount"] == 0
    assert set(lock["lineage"]["anchors"]) == {
        "P2-06_A4_RIDGE",
        "P2-07_A4_XGB",
        "P2-08_A5_XGB",
    }
    assert lock["isolation"] == {
        "finalHoldoutArtifactsAllowed": False,
        "formalCandidatesAllowed": False,
        "phase2OverlaysUnlocked": False,
    }
    assert lock["publishingAuthorized"] is False
    assert (
        write_phase3_contract_lock(
            phase2_acceptance=acceptance,
            phase2_evidence=evidence,
            contract_path="configs/research/ashare_phase3_v1.yaml",
            output=path,
        )
        == path
    )


def test_phase3_cli_exposes_only_diagnostic_pr_a_commands():
    validate = parser().parse_args(
        [
            "phase3-validate",
            "--phase2-acceptance",
            "acceptance.json",
            "--phase2-evidence",
            "evidence.json",
            "--output",
            "lock.json",
        ]
    )
    diagnose = parser().parse_args(
        [
            "phase3-diagnose",
            "--contract-lock",
            "lock.json",
            "--evidence",
            "evidence.json",
            "--output",
            "diagnosis",
        ]
    )

    assert validate.contract == "configs/research/ashare_phase3_v1.yaml"
    assert diagnose.regimes == "configs/regimes/ashare_regime_v1.yaml"
    with pytest.raises(SystemExit):
        parser().parse_args(["phase3-confirmation-freeze"])
