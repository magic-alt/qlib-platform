from __future__ import annotations

import json

from tushare_qlib.cli import _report_payload, parser
from tushare_qlib.settings import Paths, Settings
from tushare_qlib.standalone_status import collect_status


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


def test_cli_defaults_to_standalone_profile():
    args = parser().parse_args(["status", "--json"])

    assert args.config == "configs/pipeline.standalone.yaml"
    assert args.command == "status"
    assert args.as_json is True


def test_parser_exposes_standalone_auth_and_release_commands():
    auth_args = parser().parse_args(["auth", "user-list"])
    release_args = parser().parse_args(["release", "verify", "ds_" + "0" * 64])

    assert auth_args.auth_command == "user-list"
    assert release_args.release_command == "verify"
    assert parser().parse_args(["health", "dependencies"]).kind == "dependencies"


def test_status_is_ready_without_platform_and_reports_missing_data(tmp_path):
    paths = Paths.from_root(tmp_path / "data")
    settings = Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={"mode": "standalone", "data_source": {"kind": "local"}, "qlib": {}},
        paths=paths,
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=paths.root / "qlib" / "current",
    )

    payload = collect_status(settings)

    assert payload["configuration"] == "ready"
    assert payload["auth"] == "uninitialized"
    assert payload["research"] == "data_unavailable"
    assert payload["platform"] == "not_configured"
    assert not paths.root.exists()
