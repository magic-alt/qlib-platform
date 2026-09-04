from __future__ import annotations

import json
import sys

import pytest

from qlib_platform.cli import _report_payload, main, parser
from qlib_platform.releases.capabilities import ReleaseCapabilityError
from qlib_platform.settings import Paths, Settings
from qlib_platform.runtime.standalone_status import collect_status


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


@pytest.mark.parametrize(
    ("command", "arguments", "capability"),
    [
        ("phase2-plan", ["--contract-lock", "lock.json", "--output", "plan.json"], "phase2"),
        ("phase3-plan", ["--contract-lock", "lock.json", "--output", "plan.json"], "phase3"),
    ],
)
def test_governed_phase_commands_require_current_release_capability(
    tmp_path, monkeypatch, command, arguments, capability
):
    config = tmp_path / "pipeline.yaml"
    config.write_text(
        "\n".join(
            [
                "mode: standalone",
                f"project_root: {tmp_path / 'data'}",
                "data_source: {kind: auto}",
                "qlib: {}",
            ]
        ),
        encoding="utf-8",
    )
    calls: list[str] = []

    def reject(_settings, requested, **_kwargs):
        calls.append(requested)
        raise ReleaseCapabilityError("guard-called")

    monkeypatch.setattr("qlib_platform.settings.load_dotenv", lambda: None)
    monkeypatch.setattr(
        "qlib_platform.releases.capabilities.require_release_capability",
        reject,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["tq", "--config", str(config), command, *arguments],
    )

    with pytest.raises(ReleaseCapabilityError, match="guard-called"):
        main()

    assert calls == [capability]


@pytest.mark.parametrize(
    ("command", "arguments", "capability"),
    [
        ("build-target-portfolio", [], "target_portfolio"),
        ("lean-export", ["target.csv"], "target_portfolio"),
        ("lean-register", ["bundle.json"], "artifact_v2_export"),
        (
            "artifact-v2-export",
            [
                "manifest.json",
                "--output-dir",
                "bundle",
                "--git-commit",
                "abc123",
                "--container-digest",
                "sha256:test",
                "--data-release-id",
                "ds_" + "a" * 64,
            ],
            "artifact_v2_export",
        ),
    ],
)
def test_handoff_commands_check_release_capability_before_writing_or_network(
    tmp_path, monkeypatch, command, arguments, capability
):
    config = tmp_path / "pipeline.yaml"
    config.write_text(
        "\n".join(
            [
                "mode: standalone",
                f"project_root: {tmp_path / 'data'}",
                "data_source: {kind: auto}",
                "qlib: {}",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    calls: list[tuple[str, str | None]] = []

    def reject(_settings, requested, *, reference=None):
        calls.append((requested, reference))
        raise ReleaseCapabilityError("guard-called")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("qlib_platform.settings.load_dotenv", lambda: None)
    monkeypatch.setattr(
        "qlib_platform.releases.capabilities.require_release_capability",
        reject,
    )
    monkeypatch.setattr(
        "qlib_platform.releases.capabilities.data_release_id_from_artifact",
        lambda _path: "ds_" + "a" * 64,
    )
    monkeypatch.setattr(
        "qlib_platform.releases.capabilities.data_release_id_from_bundle",
        lambda _path: "ds_" + "a" * 64,
    )
    monkeypatch.setattr(
        "qlib_platform.backtesting.trade_plan.resolve_selection_path",
        lambda *_args, **_kwargs: tmp_path / "selection.csv",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["tq", "--config", str(config), command, *arguments],
    )

    with pytest.raises(ReleaseCapabilityError, match="guard-called"):
        main()

    assert calls == [(capability, "ds_" + "a" * 64)]
