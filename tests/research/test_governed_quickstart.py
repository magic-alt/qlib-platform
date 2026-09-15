from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from qlib_platform.research.workflow import governed_quickstart as governed
from qlib_platform.settings import Paths, Settings


def _settings(tmp_path: Path) -> Settings:
    config = tmp_path / "config.yaml"
    config.write_text("mode: standalone\n")
    paths = Paths.from_root(tmp_path / "data")
    paths.mkdirs()
    qlib = tmp_path / "qlib"
    qlib.mkdir()
    return Settings(
        config_path=config,
        data={},
        paths=paths,
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=qlib,
    )


def _args(**overrides):
    values = {
        "config": "configs/pipeline.standalone.yaml",
        "dataset_ref": "research-release-current",
        "verify_mode": "deep",
        "sample_size": 4,
        "workers": 1,
        "max_concurrent_jobs": 1,
        "max_memory_gb": 8.0,
        "max_disk_gb": 10.0,
        "mode": "fixed",
        "stage": "signal",
        "prediction_backtest": False,
        "verbose_child_output": False,
        "dry_run": False,
        "continue_on_error": False,
        "benchmark": "SH000300",
        "topn": 30,
        "output": None,
        "command": "run",
        "alpha_pack": ["alpha158_market_v1"],
        "model": ["lightgbm"],
        "model_profile": [],
        "template": None,
        "artifact_level": "full",
        "train": None,
        "valid": None,
        "test": None,
        "start": None,
        "end": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _plan(tmp_path: Path, *, prediction_backtest: bool = False):
    config = tmp_path / "job.yaml"
    config.write_text("experiment:\n  alpha:\n    pack: alpha158_market_v1\n")
    manifest = tmp_path / "manifest.json"
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"payload")
    manifest.write_text(
        json.dumps({"artifacts": [{"name": "oos_predictions.parquet", "localPath": str(artifact)}]})
    )
    return {
        "researchId": "research-test",
        "datasetRef": "research-release-current",
        "researchSpec": {
            "dataset": {"reference": "research-release-current", "versionId": "v1", "dataReleaseId": "r1"},
            "verification": {"mode": "deep"},
        },
        "predictionBacktest": prediction_backtest,
        "jobs": [
            {
                "cellId": "cell-a",
                "scientificInputHash": "input-a",
                "alphaPack": "alpha158_market_v1",
                "model": "lightgbm",
                "config": str(config),
                "modelProfile": str(tmp_path / "model.yaml"),
                "command": ["python", "-m", "fake-train"],
            }
        ],
        "_manifest": manifest,
        "_artifact": artifact,
    }


def test_governed_run_executes_then_reuses_verified_stages(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    args = _args()
    plan = _plan(tmp_path)
    manifest = plan.pop("_manifest")
    plan.pop("_artifact")

    monkeypatch.setattr(
        governed.legacy,
        "_verify",
        lambda settings, ref, args: {
            "path": str(tmp_path / "dataset"),
            "versionId": "v1",
            "dataReleaseId": "r1",
        },
    )
    monkeypatch.setattr(governed, "assert_alpha_pack_compatible", lambda *args, **kwargs: None)
    calls = []

    def execute(command, **kwargs):
        calls.append(command)
        if "runtime-probe" in command:
            return 0, {"resolvedDevice": "cpu"}, []
        return 0, {"manifest": str(manifest)}, []

    monkeypatch.setattr(governed.legacy, "_execute", execute)
    monkeypatch.setattr(governed.legacy, "_attach_summary", lambda settings, job, result: result)
    monkeypatch.setattr(governed.legacy, "_write_matrix", lambda root, payload: None)

    assert governed._run(settings, args, plan, tmp_path / "out") == 0
    assert plan["jobs"][0]["status"] == "SUCCEEDED"
    assert len(calls) == 2

    calls.clear()
    plan2 = _plan(tmp_path)
    plan2.pop("_manifest")
    plan2.pop("_artifact")
    assert governed._run(settings, args, plan2, tmp_path / "out") == 0
    assert plan2["jobs"][0]["status"] == "REUSED"
    assert calls == []


def test_governed_run_prediction_backtest_and_corruption_retry(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    args = _args()
    plan = _plan(tmp_path, prediction_backtest=True)
    manifest = plan.pop("_manifest")
    artifact = plan.pop("_artifact")
    predictions = tmp_path / "predictions.parquet"
    predictions.write_bytes(b"pred")

    monkeypatch.setattr(
        governed.legacy,
        "_verify",
        lambda *a, **k: {"path": str(tmp_path / "dataset"), "versionId": "v1", "dataReleaseId": "r1"},
    )
    monkeypatch.setattr(governed, "assert_alpha_pack_compatible", lambda *a, **k: None)
    monkeypatch.setattr(governed.legacy, "_attach_summary", lambda settings, job, result: result)
    monkeypatch.setattr(governed.legacy, "_predictions", lambda result: predictions)
    monkeypatch.setattr(governed, "summarize_result", lambda *a, **k: {"gate": "PASS"})
    monkeypatch.setattr(governed.legacy, "_write_matrix", lambda *a, **k: None)
    sequence = [
        (0, {"resolvedDevice": "cpu"}, []),
        (0, {"manifest": str(manifest)}, []),
        (0, {"manifest": str(manifest)}, []),
    ]
    monkeypatch.setattr(governed.legacy, "_execute", lambda *a, **k: sequence.pop(0))

    assert governed._run(settings, args, plan, tmp_path / "bt") == 0
    assert plan["jobs"][0]["predictionBacktest"]["exitCode"] == 0

    artifact.write_bytes(b"corrupt")
    assert governed.RunState(tmp_path / "bt" / "run_state.json", "research-test").decide(
        "job.cell-a.research",
        governed.identity(
            {
                "cellId": "cell-a",
                "scientificInputHash": "input-a",
                "datasetVersionId": "v1",
                "dataReleaseId": "r1",
                "stage": "research",
            },
            prefix="stage-",
        ),
    ).reuse is False


def test_governed_run_dry_run_and_runtime_failure(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(
        governed.legacy,
        "_verify",
        lambda *a, **k: {"path": str(tmp_path / "dataset"), "versionId": "v1", "dataReleaseId": "r1"},
    )
    monkeypatch.setattr(governed, "assert_alpha_pack_compatible", lambda *a, **k: None)
    monkeypatch.setattr(governed.legacy, "_write_matrix", lambda *a, **k: None)

    dry = _plan(tmp_path)
    dry.pop("_manifest")
    dry.pop("_artifact")
    assert governed._run(settings, _args(dry_run=True), dry, tmp_path / "dry") == 0
    assert dry["status"] == "DRY_RUN"

    failed = _plan(tmp_path)
    failed.pop("_manifest")
    failed.pop("_artifact")
    monkeypatch.setattr(governed.legacy, "_execute", lambda *a, **k: (2, None, ["runtime warning"]))
    assert governed._run(settings, _args(), failed, tmp_path / "failed") == 2
    assert failed["jobs"][0]["status"] == "RUNTIME_UNAVAILABLE"


def test_governed_plan_carries_identity_budget_and_cell_hash(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    args = _args(command="plan", output=str(tmp_path / "planned"))
    profile = tmp_path / "profile.yaml"
    profile.write_text("family: lightgbm\nparams:\n  seed: 7\n")
    config = tmp_path / "generated.yaml"
    config.write_text("experiment:\n  alpha:\n    pack: alpha158_market_v1\n")
    anchor = {"path": str(tmp_path / "dataset"), "versionId": "v1", "dataReleaseId": "r1"}

    monkeypatch.setattr(governed, "_read_only_dataset_anchor", lambda *a: (anchor, None))
    monkeypatch.setattr(governed.legacy, "_selected", lambda args: (("alpha158_market_v1",), (("lgb", profile),)))
    monkeypatch.setattr(governed, "get_research_template", lambda value: None)
    monkeypatch.setattr(
        governed.legacy,
        "_dataset_ref",
        lambda settings, requested: requested or "research-release-current",
    )
    monkeypatch.setattr(
        governed.legacy,
        "build_plan",
        lambda settings, args, root: {
            "datasetRef": "research-release-current",
            "mode": "fixed",
            "predictionBacktest": False,
            "jobs": [
                {
                    "alphaPack": "alpha158_market_v1",
                    "model": "lgb",
                    "config": str(config),
                    "modelProfile": str(profile),
                    "command": ["python", "train"],
                }
            ],
        },
    )
    monkeypatch.setattr(governed, "_state_text", lambda: "Final holdout | `SEALED`; access disallowed")

    plan, root = governed._governed_plan(settings, args)
    assert root == Path(args.output).resolve()
    assert plan["researchId"].startswith("research-")
    assert plan["jobs"][0]["cellId"].startswith("cell-")
    assert plan["jobs"][0]["scientificInputHash"].startswith("input-")
    assert plan["resourceBudget"]["maxConcurrentJobs"] == 1
    assert plan["datasetPlanStatus"] == "READY"


def test_manifest_outputs_and_run_stage_failure(tmp_path: Path) -> None:
    missing = governed._manifest_outputs({"manifest": str(tmp_path / "missing.json")})
    assert missing == []

    manifest = tmp_path / "manifest.json"
    artifact = tmp_path / "artifact"
    artifact.write_text("x")
    manifest.write_text(json.dumps({"artifacts": [{"localPath": str(artifact)}]}))
    assert governed._manifest_outputs({"manifest": str(manifest)}) == [manifest, artifact]

    state = governed.RunState(tmp_path / "state.json", "research-x")

    def boom():
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        governed._run_stage(state, tmp_path, "stage", {"x": 1}, boom)
    assert state.payload["stages"]["stage"]["status"] == "FAILED"


class _Parser:
    def __init__(self, args):
        self.args = args

    def parse_args(self):
        return self.args


def test_main_plan_and_run_paths(tmp_path: Path, monkeypatch, capsys) -> None:
    settings = _settings(tmp_path)
    plan = {
        "status": "PLANNED",
        "datasetPlanStatus": "READY",
        "researchId": "research-main",
        "jobs": [],
    }
    monkeypatch.setattr(governed.Settings, "load", lambda *a, **k: settings)
    monkeypatch.setattr(governed.legacy, "_write_matrix", lambda *a, **k: None)

    plan_args = _args(command="plan")
    monkeypatch.setattr(governed, "_parser", lambda: _Parser(plan_args))
    monkeypatch.setattr(governed, "_governed_plan", lambda *a, **k: (dict(plan), tmp_path / "plan"))
    assert governed.main() == 0
    assert '"researchId": "research-main"' in capsys.readouterr().out

    run_args = _args(command="run")
    run_plan = dict(plan)
    run_plan["status"] = "SUCCEEDED"
    monkeypatch.setattr(governed, "_parser", lambda: _Parser(run_args))
    monkeypatch.setattr(governed, "_governed_plan", lambda *a, **k: (run_plan, tmp_path / "run"))
    monkeypatch.setattr(governed, "_run", lambda *a, **k: 0)
    monkeypatch.setattr(governed, "render_terminal_summary", lambda *a, **k: "summary")
    assert governed.main() == 0
    assert "summary" in capsys.readouterr().out


def test_main_rejects_governance(tmp_path: Path, monkeypatch, capsys) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(governed.Settings, "load", lambda *a, **k: settings)
    monkeypatch.setattr(governed, "_parser", lambda: _Parser(_args(command="run")))

    def rejected(*a, **k):
        raise PermissionError("sealed")

    monkeypatch.setattr(governed, "_governed_plan", rejected)
    assert governed.main() == 2
    assert "REJECTED" in capsys.readouterr().out
