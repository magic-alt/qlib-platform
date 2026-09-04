import json
from pathlib import Path
from types import SimpleNamespace

from qlib_platform.research.research_cli_ux import (
    filter_known_child_noise,
    render_terminal_summary,
    result_manifest_path,
    summarize_result,
)
from qlib_platform.research.research_quickstart import _attach_summary, parser


def _write_manifest(output_root: Path, run_id: str = "run-123") -> Path:
    path = output_root / "research" / run_id / "manifest.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "externalRunId": run_id,
                "dataset": {"versionId": "dataset-v1", "datasetId": "cn_standalone"},
                "runtime": {
                    "modelProfile": "lightgbm_auto",
                    "resolvedDevice": "gpu:0",
                    "deviceName": "NVIDIA GeForce RTX 5060",
                },
                "metrics": {
                    "ic_mean": 0.025,
                    "icir": 0.129,
                    "rank_ic_mean": 0.053,
                    "rank_icir": 0.302,
                },
                "promotion": {"status": "REJECTED", "decision": "REJECT"},
                "featureStore": {
                    "featureSnapshotId": "fs_abc",
                    "cacheStatus": "REUSED",
                },
                "timings": {
                    "phasesSeconds": {
                        "feature_store_seconds": 2.5,
                        "train_seconds": 5.3,
                        "predict_seconds": 0.3,
                    },
                    "totalSeconds": 12.4,
                    "peakRssMb": 3904.5,
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_known_child_noise_filter_is_narrow_and_preserves_failures() -> None:
    raw = "\n".join(
        [
            "ModuleNotFoundError. CatBoostModel are skipped. (optional)",
            "Gym has been unmaintained since 2022 and does not support NumPy 2.0",
            "1 warning generated.",
            "Downloading artifacts: 100%|##########| 1/1",
            "D:/repo/.venv/Lib/site-packages/qlib/data/dataset/processor.py:358: SettingWithCopyWarning:",
            "A value is trying to be set on a copy of a slice from a DataFrame.",
            "df[cols] = t",
            "Traceback (most recent call last):",
            "ValueError: real failure",
        ]
    )

    filtered = filter_known_child_noise(raw)

    assert "CatBoostModel are skipped" not in filtered
    assert "Gym has been unmaintained" not in filtered
    assert "warning generated" not in filtered
    assert "Downloading artifacts" not in filtered
    assert "SettingWithCopyWarning" not in filtered
    assert "Traceback (most recent call last):" in filtered
    assert "ValueError: real failure" in filtered


def test_result_manifest_falls_back_to_run_id(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path)

    assert result_manifest_path(tmp_path, {"runId": "run-123"}) == manifest


def test_summary_exposes_device_gate_cache_metrics_and_timings(tmp_path: Path) -> None:
    _write_manifest(tmp_path)

    summary = summarize_result(tmp_path, {"runId": "run-123"})

    assert summary is not None
    assert summary["datasetVersionId"] == "dataset-v1"
    assert summary["deviceName"] == "NVIDIA GeForce RTX 5060"
    assert summary["resolvedDevice"] == "gpu:0"
    assert summary["decision"] == "REJECT"
    assert summary["featureCacheStatus"] == "REUSED"
    assert summary["featureSnapshotId"] == "fs_abc"
    assert summary["metrics"]["icir"] == 0.129
    assert summary["timings"]["feature_store_seconds"] == 2.5
    assert summary["totalSeconds"] == 12.4


def test_attach_summary_backfills_manifest_for_prediction_backtest(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path)
    settings = SimpleNamespace(paths=SimpleNamespace(output=tmp_path))
    job = {}
    result = {"runId": "run-123"}

    attached = _attach_summary(settings, job, result)

    assert attached is result
    assert result["manifest"] == str(manifest)
    assert job["summary"]["featureCacheStatus"] == "REUSED"


def test_terminal_summary_is_human_readable_and_machine_json_can_remain_last(tmp_path: Path) -> None:
    summary = {
        "deviceName": "NVIDIA GeForce RTX 5060",
        "resolvedDevice": "gpu:0",
        "decision": "REJECT",
        "featureCacheStatus": "REUSED",
        "featureSnapshotId": "fs_abc",
        "metrics": {"ic_mean": 0.025, "icir": 0.129, "rank_ic_mean": 0.053, "rank_icir": 0.302},
        "timings": {"feature_store_seconds": 2.5, "train_seconds": 5.3},
        "totalSeconds": 12.4,
        "peakRssMb": 3904.5,
        "manifest": str(tmp_path / "manifest.json"),
    }
    plan = {
        "status": "SUCCEEDED",
        "mode": "fixed",
        "stage": "signal",
        "dataset": {"versionId": "dataset-v1"},
        "jobs": [
            {
                "status": "SUCCEEDED",
                "alphaPack": "alpha158_market_v1",
                "model": "lightgbm",
                "summary": summary,
                "predictionBacktest": {"exitCode": 0},
            }
        ],
    }

    rendered = render_terminal_summary(plan, tmp_path)

    assert "Research quickstart: SUCCEEDED" in rendered
    assert "DatasetVersion: dataset-v1" in rendered
    assert "NVIDIA GeForce RTX 5060 (gpu:0)" in rendered
    assert "ICIR 0.1290" in rendered
    assert "Gate: REJECT" in rendered
    assert "Feature cache: REUSED (fs_abc)" in rendered
    assert "Prediction backtest: SUCCEEDED" in rendered


def test_verbose_child_output_is_opt_in() -> None:
    command_parser = parser()

    assert command_parser.parse_args(["run"]).verbose_child_output is False
    assert command_parser.parse_args(["run", "--verbose-child-output"]).verbose_child_output is True
