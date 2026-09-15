from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from qlib_platform.research.workflow.control import (
    RunState,
    build_research_spec,
    enforce_governance,
    expand_matrix,
    identity,
    research_lock,
)


def _profile(path: Path, *, seed: int = 7) -> Path:
    path.write_text(yaml.safe_dump({"family": "lightgbm", "params": {"seed": seed, "num_leaves": 31}}))
    return path


def _spec(tmp_path: Path, *, seed: int = 7, release: str = "release-a"):
    profile = _profile(tmp_path / f"model-{seed}.yaml", seed=seed)
    return build_research_spec(
        dataset_ref="research-release-current",
        dataset_anchor={"versionId": "v1", "dataReleaseId": release},
        mode="fixed",
        template=None,
        stage="signal",
        alpha_packs=("alpha158_market_v1",),
        model_profiles=(("lightgbm", profile),),
        train=("2020-01-01", "2021-01-01"),
        valid=("2021-01-02", "2021-06-01"),
        test=("2021-06-02", "2022-01-01"),
        start=None,
        end=None,
        benchmark="SH000300",
        topn=30,
        artifact_level="full",
        prediction_backtest=True,
        verification={"mode": "deep", "workers": 4},
    )


def test_research_identity_is_deterministic_and_scientific(tmp_path: Path) -> None:
    first = _spec(tmp_path, seed=7)
    second = _spec(tmp_path, seed=7)
    changed_seed = _spec(tmp_path, seed=8)
    changed_release = _spec(tmp_path, seed=7, release="release-b")
    assert first["researchId"] == second["researchId"]
    assert first["researchId"] != changed_seed["researchId"]
    assert first["researchId"] != changed_release["researchId"]
    assert first["research"]["models"][0]["profile"]["parameters"]["params"]["seed"] == 7


def test_matrix_cell_ids_do_not_depend_on_unrelated_matrix_members(tmp_path: Path) -> None:
    a = _profile(tmp_path / "a.yaml", seed=1)
    b = _profile(tmp_path / "b.yaml", seed=2)
    one = expand_matrix(("alpha158_market_v1",), (("a", a),), research_id="research-one")
    two = expand_matrix(("alpha158_market_v1",), (("a", a), ("b", b)), research_id="research-two")
    assert one[0]["cellId"] == two[0]["cellId"]


def test_resume_reuses_only_hash_verified_outputs_and_records_attempts(tmp_path: Path) -> None:
    output = tmp_path / "artifact.json"
    output.write_text('{"ok": true}')
    state = RunState(tmp_path / "run_state.json", "research-a")
    input_hash = identity({"seed": 7})
    assert state.decide("train", input_hash).reuse is False
    state.start("train", input_hash)
    state.finish("train", status="SUCCEEDED", output_paths=[output], metadata={"exitCode": 0})
    assert state.decide("train", input_hash).reuse is True

    output.write_text('{"ok": false}')
    decision = state.decide("train", input_hash)
    assert decision.reuse is False
    assert "hash mismatch" in decision.reason
    state.start("train", input_hash)
    assert state.payload["stages"]["train"]["attempt"] == 2
    assert state.payload["history"][0]["status"] == "SUCCEEDED"


def test_run_state_keeps_unaffected_stage_cache_across_plan_identity_change(tmp_path: Path) -> None:
    output = tmp_path / "artifact"
    output.write_text("stable")
    path = tmp_path / "run_state.json"
    state = RunState(path, "research-a")
    stage_hash = identity({"cell": "a", "dataset": "release-a"})
    state.start("job.cell-a.research", stage_hash)
    state.finish("job.cell-a.research", status="SUCCEEDED", output_paths=[output])
    changed = RunState(path, "research-b")
    assert changed.decide("job.cell-a.research", stage_hash).reuse is True
    assert "research-a" in changed.payload["priorResearchIds"]


def test_lock_rejects_concurrent_writer(tmp_path: Path) -> None:
    lock = tmp_path / ".research.lock"
    with research_lock(lock):
        with pytest.raises(RuntimeError, match="already leased"):
            with research_lock(lock):
                pass
    assert not lock.exists()


def test_phase3_d_fail_closed_but_future_authorized_policy_can_run() -> None:
    sealed = """
    Formal candidates | Disallowed
    Model selection | Disallowed
    Final holdout | `SEALED`; access disallowed
    Publishing in Phase 3-D | Disabled
    """
    with pytest.raises(PermissionError, match="Phase 3-D"):
        enforce_governance(stage="release", mode="fixed", current_state_text=sealed)

    authorized = """
    Formal candidates | Allowed
    Model selection | Allowed
    Final holdout | OPEN
    Publishing in Phase 4 | Enabled
    """
    policy = enforce_governance(stage="release", mode="fixed", current_state_text=authorized)
    assert all(policy.values())
