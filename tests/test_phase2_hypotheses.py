from __future__ import annotations

import json
from pathlib import Path

import pytest

from tushare_qlib.cli import parser
from tushare_qlib.research.phase2_contract import write_phase2_contract_lock
from tushare_qlib.research.phase2_hypotheses import (
    bind_phase2_hypothesis,
    hypothesis_definition_sha256,
    hypothesis_feature_set,
)


def _lock(tmp_path: Path) -> Path:
    phase1 = tmp_path / "phase1.json"
    phase1.write_text(
        json.dumps(
            {
                "schemaVersion": "alpha_phase1_synthesis_v1",
                "studyId": "phase1-test",
                "status": {"phase1Completion": "COMPLETE"},
                "primaryRecommendation": "REGIME_AWARE_RESEARCH",
                "selectionUsesFinalHoldout": False,
                "publishingAuthorized": False,
                "evidence": {},
            }
        ),
        encoding="utf-8",
    )
    return write_phase2_contract_lock(
        phase1_manifest=phase1,
        contract_path="configs/research/ashare_phase2_v1.yaml",
        output=tmp_path / "lock.json",
    )


def test_hypothesis_feature_sets_are_nested_and_specific():
    h001_baseline = hypothesis_feature_set("H001", "baseline")
    h001_candidate = hypothesis_feature_set("H001", "candidate")
    h104_baseline = hypothesis_feature_set("H104", "baseline")
    h104_candidate = hypothesis_feature_set("H104", "candidate")

    assert h001_candidate.features == (*h001_baseline.features, "EARNINGS_YIELD")
    assert h104_candidate.features == (*h104_baseline.features, "PROFITABILITY_X_LOWVOL")
    assert "VALUE_X_VOLATILITY" not in h104_candidate.features
    assert h001_candidate.fingerprint != h104_candidate.fingerprint


def test_binding_uses_frozen_contract_definition(tmp_path: Path):
    lock_path = _lock(tmp_path)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    hypothesis = next(item for item in lock["contract"]["hypotheses"] if item["hypothesis_id"] == "H003")
    binding = bind_phase2_hypothesis(lock_path, "h003", "candidate")
    manifest = binding.to_manifest()

    assert binding.feature_set_id == "H003_CANDIDATE"
    assert binding.hypothesis_definition_sha256 == hypothesis_definition_sha256(hypothesis)
    assert manifest["hypothesisId"] == "H003"
    assert manifest["featureSetId"] == "H003_CANDIDATE"
    assert manifest["hypothesisDefinitionSha256"] == binding.hypothesis_definition_sha256
    assert manifest["hypothesisBindingSha256"] == binding.fingerprint
    assert "hypothesis_id" not in manifest


def test_research_run_parser_accepts_explicit_hypothesis_binding():
    args = parser().parse_args(
        [
            "research-run",
            "--hypothesis-id",
            "H104",
            "--hypothesis-role",
            "candidate",
            "--contract-lock",
            "lock.json",
        ]
    )
    assert args.hypothesis_id == "H104"
    assert args.hypothesis_role == "candidate"
    with pytest.raises(SystemExit):
        parser().parse_args(["research-run", "--hypothesis-role", "invalid"])
