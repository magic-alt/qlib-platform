from __future__ import annotations

import json
from pathlib import Path

from qlib_platform.research.phase2_contract import write_phase2_contract_lock
from qlib_platform.research.phase2_program import (
    write_incremental_acceptance,
    write_phase2_experiment_plan,
)


def _lock(tmp_path: Path, recommendation: str) -> Path:
    phase1 = tmp_path / f"phase1-{recommendation}.json"
    phase1.write_text(
        json.dumps(
            {
                "schemaVersion": "alpha_phase1_synthesis_v1",
                "studyId": "phase1-test",
                "status": {"phase1Completion": "COMPLETE"},
                "primaryRecommendation": recommendation,
                "selectionUsesFinalHoldout": False,
                "publishingAuthorized": False,
                "evidence": {"modelExplanation": {"boundedSensitivity": "RECOVERABLE"}},
            }
        ),
        encoding="utf-8",
    )
    return write_phase2_contract_lock(
        phase1_manifest=phase1,
        contract_path="configs/research/ashare_phase2_v1.yaml",
        output=tmp_path / f"lock-{recommendation}.json",
    )


def _metrics(**overrides: float) -> dict[str, float]:
    values = {
        "coverage": 0.9,
        "oriented_rank_ic": 0.02,
        "positive_fold_ratio": 0.8,
        "hac_t": 3.0,
        "bh_q_value": 0.01,
        "local_fdr": 0.05,
        "romano_wolf_p_value": 0.01,
        "incremental_rank_ic": 0.003,
        "incremental_hac_t": 2.2,
        "worst_fold_rank_ic": 0.0,
        "worst_rolling_rank_ic": 0.0,
        "leave_one_year_min_mean": 0.005,
        "leave_one_year_retention": 0.8,
        "turnover_increase": 0.1,
        "stressed_net_spread": 0.001,
    }
    values.update(overrides)
    return values


def _candidate(metrics: dict[str, float]) -> dict[str, object]:
    return {
        "candidateId": "H001-ridge-A1",
        "hypothesisId": "H001",
        "alphaPack": "ashare_alpha_phase2_v1",
        "featureSet": "A1",
        "model": "ridge",
        "portfolio": "topk_dropout_v1",
        "regimeRule": "none",
        "metrics": metrics,
    }


def test_alpha_route_emits_fixed_matrix_without_holdout(tmp_path: Path):
    path = write_phase2_experiment_plan(
        contract_lock=_lock(tmp_path, "ALPHA_PACK_V2"),
        output=tmp_path / "plan.json",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert [item["experimentId"] for item in payload["experiments"]] == [
        f"P2-{number:02d}" for number in range(1, 11)
    ]
    assert all(item["usesFinalHoldout"] is False for item in payload["experiments"])
    assert payload["publishingAuthorized"] is False


def test_portfolio_and_no_go_routes_preserve_strict_order(tmp_path: Path):
    portfolio = json.loads(
        write_phase2_experiment_plan(
            contract_lock=_lock(tmp_path, "PORTFOLIO_CONSTRUCTION"),
            output=tmp_path / "portfolio.json",
        ).read_text(encoding="utf-8")
    )
    no_go = json.loads(
        write_phase2_experiment_plan(
            contract_lock=_lock(tmp_path, "NO_GO_NEW_ALPHA"),
            output=tmp_path / "no-go.json",
        ).read_text(encoding="utf-8")
    )

    assert portfolio["experiments"][0]["experimentId"] == "P2-PC01"
    assert no_go["experiments"] == []


def test_incremental_acceptance_records_explicit_rejection_reasons(tmp_path: Path):
    path = write_incremental_acceptance(
        contract_lock=_lock(tmp_path, "ALPHA_PACK_V2"),
        candidates=[_candidate(_metrics(stressed_net_spread=-0.001))],
        output=tmp_path / "acceptance.json",
    )
    candidate = json.loads(path.read_text(encoding="utf-8"))["candidates"][0]

    assert candidate["status"] == "REJECTED"
    assert candidate["gatePass"] is False
    assert candidate["rejectionReasons"] == ["STRESSED_COST"]
    assert candidate["featureSet"] == "A1"
