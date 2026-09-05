from __future__ import annotations

import json
from pathlib import Path

from qlib_platform.research.reporting.web_dashboard import (
    build_dashboard_data,
    render_dashboard,
    resolve_matrix,
    write_dashboard,
)


def _matrix(profile: Path) -> dict[str, object]:
    return {
        "schemaVersion": "1.0",
        "createdAtUtc": "2026-09-05T04:15:10+00:00",
        "datasetRef": "standalone-current",
        "mode": "fixed",
        "stage": "signal",
        "status": "SUCCEEDED",
        "dataset": {
            "versionId": "dataset-v1",
            "dataReleaseId": "release-v1",
            "verification": {"mode": "deep", "status": "VERIFIED"},
        },
        "observedWarnings": ["$open field data contains nan"],
        "jobs": [
            {
                "alphaPack": "alpha158_market_v1",
                "model": "lightgbm",
                "modelProfile": str(profile),
                "status": "SUCCEEDED",
                "command": ["python", "-m", "qlib_platform", "train-select"],
                "runtime": {"resolvedDevice": "cpu"},
                "summary": {
                    "decision": "REJECT",
                    "resolvedDevice": "cpu",
                    "metrics": {
                        "ic_mean": 0.016398,
                        "icir": 0.078963,
                        "rank_ic_mean": 0.041730,
                        "rank_icir": 0.217522,
                        "long_short_annualized": 0.175069,
                    },
                    "timings": {"train_seconds": 7.306, "predict_seconds": 0.177},
                    "totalSeconds": 12.503,
                    "peakRssMb": 3730.7,
                },
                "predictionBacktest": {
                    "summary": {
                        "metrics": {
                            "excess_ir": -0.468365,
                            "max_drawdown": -0.216843,
                        }
                    }
                },
            }
        ],
    }


def test_dashboard_interprets_unstable_ranking_signal(tmp_path: Path) -> None:
    profile = tmp_path / "lightgbm.yaml"
    profile.write_text(
        "name: lightgbm_auto\nfamily: lightgbm\ndevice: auto\nmodel_kwargs:\n  max_bin: 63\n",
        encoding="utf-8",
    )
    data = build_dashboard_data(_matrix(profile))
    job = data["jobs"][0]

    assert job["decision"] == "REJECT"
    assert job["signal"]["rank_ic_mean"] == 0.041730
    assert any("day-to-day stability" in note for note in job["analysis"])
    assert any(row["label"] == "RankICIR" and row["passed"] is False for row in job["gates"])


def test_dashboard_is_self_contained_and_escapes_content(tmp_path: Path) -> None:
    profile = tmp_path / "lightgbm.yaml"
    profile.write_text("name: '<script>alert(1)</script>'\nfamily: lightgbm\n", encoding="utf-8")
    html = render_dashboard(build_dashboard_data(_matrix(profile)))

    assert "<style>" in html
    assert "Research Dashboard" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html
    assert "$open field data contains nan" in html


def test_write_dashboard_and_latest_resolution(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yaml"
    profile.write_text("name: lightgbm_auto\nfamily: lightgbm\n", encoding="utf-8")
    quickstart = tmp_path / "data" / "output" / "quickstart" / "20260905T041510Z-run"
    quickstart.mkdir(parents=True)
    matrix = quickstart / "research_matrix.json"
    matrix.write_text(json.dumps(_matrix(profile)), encoding="utf-8")

    assert resolve_matrix(None, latest=True, root=tmp_path) == matrix.resolve()
    output = write_dashboard(matrix)
    assert output == quickstart / "research_dashboard.html"
    assert output.is_file()
    assert "alpha158_market_v1" in output.read_text(encoding="utf-8")
