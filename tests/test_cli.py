from __future__ import annotations

import json

from tushare_qlib.cli import _report_payload


def test_report_payload_omits_uncreated_minimal_report_files(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "externalRunId": "run-1",
                "artifacts": [{"name": "timings.json", "localPath": str(tmp_path / "timings.json")}],
            }
        ),
        encoding="utf-8",
    )

    payload = _report_payload(manifest_path)

    assert payload["runId"] == "run-1"
    assert "reportMarkdown" not in payload
    assert "reportPdf" not in payload


def test_report_payload_includes_existing_report_fallbacks(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"externalRunId": "run-2"}), encoding="utf-8")
    (tmp_path / "backtest_report.md").write_text("report", encoding="utf-8")

    payload = _report_payload(manifest_path)

    assert payload["reportMarkdown"] == str(tmp_path / "backtest_report.md")
    assert "reportPdf" not in payload
