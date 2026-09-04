from __future__ import annotations

import pytest

from qlib_platform.ops.ops_state import OPS_SCHEMA_VERSION, OpsState, RunStatus


def test_task_runs_persist_attempts_artifacts_and_failures(tmp_path):
    state = OpsState(tmp_path / "ops.sqlite3")
    state.start_run("run-1", "FEEDBACK", "2026-01-07")

    attempt = state.start_task("run-1", "realized-labels", details={"input": "dr_test"})
    state.finish_task(
        "run-1",
        "realized-labels",
        attempt,
        RunStatus.FAILED,
        error_code="SOURCE_NOT_READY",
    )
    retry = state.start_task("run-1", "realized-labels")
    state.finish_task(
        "run-1",
        "realized-labels",
        retry,
        RunStatus.PASS,
        artifact_ref="rls_test",
    )
    state.finish_run("run-1", RunStatus.PASS, {"evaluation": "pes_test"})

    tasks = state.list_tasks("run-1")
    assert [item["attempt"] for item in tasks] == [1, 2]
    assert tasks[0]["error_code"] == "SOURCE_NOT_READY"
    assert tasks[1]["artifact_ref"] == "rls_test"
    with state.reading() as connection:
        version = connection.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()[
            0
        ]
    assert int(version) == OPS_SCHEMA_VERSION


def test_pipeline_cannot_finish_with_running_task(tmp_path):
    state = OpsState(tmp_path / "ops.sqlite3")
    state.start_run("run-1", "FEEDBACK", "2026-01-07")
    attempt = state.start_task("run-1", "evaluate")

    with pytest.raises(ValueError, match="unfinished task"):
        state.finish_run("run-1", RunStatus.PASS, {})
    with pytest.raises(ValueError, match="already has a RUNNING attempt"):
        state.start_task("run-1", "evaluate")

    state.finish_task("run-1", "evaluate", attempt, RunStatus.REJECTED)
    state.finish_run("run-1", RunStatus.REJECTED, {})
