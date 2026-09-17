from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import qlib_platform.cli.entrypoint as cli_entrypoint
import qlib_platform.research.evidence.run_adapters as run_adapters
import qlib_platform.research.interfaces.run_cli as run_cli
import qlib_platform.research.evidence.run_manifest as run_manifest
import qlib_platform.research.workflow.governed_entrypoint as governed_entrypoint
import qlib_platform.research.workflow.official_parity_entrypoint as parity_entrypoint
import qlib_platform.runtime.production_daily_run_entrypoint as daily_entrypoint
from qlib_platform.research.evidence.run_manifest import (
    artifact_record,
    build_run_manifest,
    compare_run_manifests,
    execute_reproduction,
    frozen_module_command,
    inspect_run,
    reproduce_run,
    resolve_run_manifest,
    store_root,
    verify_run_manifest,
    verify_runtime_context,
    write_run_manifest,
)
from qlib_platform.settings import Paths, Settings


def _settings(tmp_path: Path) -> Settings:
    config = tmp_path / "pipeline.yaml"
    config.write_text("mode: standalone\n", encoding="utf-8")
    paths = Paths.from_root(tmp_path / "data")
    paths.mkdirs()
    return Settings(
        config_path=config,
        data={
            "mode": "standalone",
            "project_root": str(paths.root),
            "data_source": {"provider": "fixture", "api_token": "secret-value"},
            "universe": {"id": "csi300", "pit": True},
            "experiment": {"label": {"horizon": 1, "cutoff": "T"}},
            "market_rules": {"cost": 0.001, "slippage": 0.0002},
        },
        paths=paths,
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=paths.qlib_versions / "dv1",
    )


def _components() -> dict[str, Any]:
    return {
        "code": {},
        "resolved_config": {},
        "environment": {},
        "data": {"data_release_id": "dr1", "dataset_version_id": "dv1"},
        "universe": {"id": "csi300", "membership_sha256": "u1"},
        "feature": {"alpha_pack": "alpha158", "feature_snapshot_id": "fs1"},
        "label": {"expression": "T+1", "horizon": 1, "cutoff": "T"},
        "split": {"train": ["2020", "2022"], "test": ["2023", "2024"]},
        "model": {"class": "LightGBM", "params": {"num_leaves": 31}, "seed": 7},
        "prediction": {"snapshot_id": "ps1"},
        "portfolio": {"strategy": "TopkDropoutStrategy", "topk": 30},
        "market": {"rule_set": "ashare-v1", "cost": 0.001},
        "benchmark": {"identity": "SH000300"},
    }


def _write_dataset(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "dataset_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "dataset_manifest_v1",
                "version_id": "dv1",
                "data_release_id": "dr1",
                "calendar": {"frequency": "day", "sha256": "calendar1"},
                "coverage": {"start": "2020-01-01", "end": "2026-09-16"},
                "source_snapshot_id": "source1",
                "watermark": "20260916",
                "universe_membership_sha256": "universe1",
            }
        ),
        encoding="utf-8",
    )


def _recompute_digest(payload: dict[str, Any]) -> None:
    payload["manifest_digest"] = run_manifest._sha256_json(run_manifest._manifest_digest_payload(payload))


def test_identity_helpers_cover_secret_lists_paths_packages_and_artifact_roles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean, refs = run_manifest.redact_secrets(
        [{"api_key": "hidden", "value": 3}, {"nested": {"password": "hidden-2"}}]
    )
    assert clean[0]["api_key"] == {"secretReference": "config:config[0].api_key"}
    assert refs == ["config:config[0].api_key", "config:config[1].nested.password"]
    assert run_manifest.canonical_business_value(
        [tmp_path / "payload.bin", {"title": "display", "value": 4}]
    ) == ["payload.bin", {"value": 4}]

    environment = run_manifest.environment_identity()
    assert len(environment["fingerprint"]) == 64
    assert "python" in environment and "packages" in environment

    seen: list[Path] = []

    def fake_git_revision(path: Path) -> dict[str, Any]:
        seen.append(path)
        return {"commit": "abc", "dirty": False}

    monkeypatch.setattr(run_manifest, "git_revision", fake_git_revision)
    code = run_manifest.project_git_identity()
    assert code["commit"] == "abc"
    assert code["dirty_policy"] == "clean-required-for-deterministic-replay"
    assert seen

    cases = {
        "model.pkl": "model",
        "predictions.csv": "prediction",
        "selection.csv": "selection",
        "portfolio.csv": "portfolio",
        "backtest_report.json": "backtest",
        "dataset_manifest.json": "dataset",
        "misc.txt": "evidence",
    }
    rows = []
    for name, role in cases.items():
        path = tmp_path / name
        path.write_text(name, encoding="utf-8")
        row = artifact_record(path)
        rows.append(row)
        assert row["role"] == role
        assert len(row["content_sha256"]) == 64

    missing = artifact_record(tmp_path / "missing.txt", required=False)
    assert missing["content_sha256"] == "MISSING"
    explicit = artifact_record(tmp_path / "missing-model.bin", role="model", required=False)
    assert explicit["role"] == "model"

    duplicated = build_run_manifest(
        source_kind="helper-test",
        status="SUCCEEDED",
        components=_components(),
        artifacts=[rows[0], rows[0]],
        warnings=["one", "one", "two"],
        known_deviations=["d", "d"],
    )
    assert len(duplicated["artifacts"]) == 1
    assert duplicated["warnings"] == ["one", "two"]
    assert duplicated["known_deviations"] == ["d"]


def test_archive_collision_reference_validation_and_manifest_structure_failures(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    manifest = build_run_manifest(source_kind="archive-test", status="FAILED", components=_components())

    bad_digest = dict(manifest)
    bad_digest["manifest_digest"] = "0" * 64
    with pytest.raises(ValueError, match="digest mismatch"):
        write_run_manifest(bad_digest, local_root=tmp_path / "bad", store=store_root(settings))

    bad_id = dict(manifest)
    bad_id["run_id"] = "mutable-latest"
    _recompute_digest(bad_id)
    with pytest.raises(ValueError, match="invalid run_id"):
        write_run_manifest(bad_id, local_root=tmp_path / "bad-id", store=store_root(settings))

    archive = write_run_manifest(manifest, local_root=tmp_path / "good", store=store_root(settings))
    assert resolve_run_manifest(store_root(settings), manifest["run_id"]) == archive
    with pytest.raises(ValueError, match="immutable run_id"):
        resolve_run_manifest(store_root(settings), "run-bad/path")
    with pytest.raises(FileNotFoundError, match="not found"):
        resolve_run_manifest(store_root(settings), "run-" + "f" * 32)

    collision = dict(manifest)
    collision["status"] = "SUCCEEDED"
    _recompute_digest(collision)
    with pytest.raises(ValueError, match="collision"):
        write_run_manifest(collision, local_root=tmp_path / "collision", store=store_root(settings))

    list_manifest = tmp_path / "list.json"
    list_manifest.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="root must be an object"):
        verify_run_manifest(list_manifest)

    wrong_schema = tmp_path / "wrong-schema.json"
    payload = dict(manifest)
    payload["run_schema_version"] = "future-v9"
    wrong_schema.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported run manifest schema"):
        verify_run_manifest(wrong_schema)

    wrong_digest = tmp_path / "wrong-digest.json"
    payload = dict(manifest)
    payload["manifest_digest"] = "bad"
    wrong_digest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest digest mismatch"):
        verify_run_manifest(wrong_digest)


def test_artifact_verification_handles_optional_invalid_entries_uri_and_semantic_tamper(
    tmp_path: Path,
) -> None:
    payload_file = tmp_path / "predictions.csv"
    payload_file.write_text("score\n1\n", encoding="utf-8")
    record = artifact_record(payload_file, role="prediction", semantic_metadata={"schema": 1})
    optional = artifact_record(tmp_path / "optional.txt", required=False)
    manifest = build_run_manifest(
        source_kind="verify-test",
        status="SUCCEEDED",
        components=_components(),
        artifacts=[record, optional],
    )
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert verify_run_manifest(path)["passed"] is True

    malformed = dict(manifest)
    malformed["artifacts"] = ["not-an-object"]
    _recompute_digest(malformed)
    path.write_text(json.dumps(malformed), encoding="utf-8")
    result = verify_run_manifest(path)
    assert result["passed"] is False
    assert result["errors"][0]["location"] == "artifacts"

    semantic = build_run_manifest(
        source_kind="verify-test",
        status="SUCCEEDED",
        components=_components(),
        artifacts=[record],
    )
    semantic["artifacts"][0]["semantic_metadata"] = {"schema": 2}
    path.write_text(json.dumps(semantic), encoding="utf-8")
    result = verify_run_manifest(path)
    assert result["passed"] is False
    assert result["errors"][0]["error"] == "artifact semantic metadata checksum mismatch"

    unsupported = dict(record)
    unsupported["local_path"] = ""
    unsupported["uri"] = "s3://bucket/predictions.csv"
    assert run_manifest._artifact_path(unsupported).name == "__unsupported_non_file_artifact__"


def test_runtime_context_compare_and_frozen_command_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    current_config = {"canonical_sha256": "cfg-now"}
    current_code = {"commit": "commit-now", "dirty": True}
    current_env = {"fingerprint": "env-now"}
    monkeypatch.setattr(run_manifest, "config_identity", lambda _: current_config)
    monkeypatch.setattr(run_manifest, "project_git_identity", lambda: current_code)
    monkeypatch.setattr(run_manifest, "environment_identity", lambda: current_env)

    expected = {
        "resolved_config": {"canonical_sha256": "cfg-old"},
        "code": {"commit": "commit-old", "dirty": False},
        "environment": {"fingerprint": "env-old"},
    }
    result = verify_runtime_context(settings, expected)
    assert result["passed"] is False
    assert {item["location"] for item in result["errors"]} == {
        "resolved_config",
        "code.git_commit",
        "code.dirty",
        "environment",
    }

    matching = {
        "resolved_config": current_config,
        "code": {"commit": "commit-now", "dirty": True},
        "environment": current_env,
    }
    assert verify_runtime_context(settings, matching)["passed"] is True

    left = build_run_manifest(source_kind="compare", status="SUCCEEDED", components=_components())
    right = json.loads(json.dumps(left))
    assert compare_run_manifests(left, right)["equivalent"] is True
    right["stage_identities"]["model_identity"] = "model-other"
    right["artifacts"] = [
        {
            "role": "prediction",
            "name": "pred.csv",
            "content_sha256": "different",
        }
    ]
    diff = compare_run_manifests(left, right)
    assert diff["equivalent"] is False
    assert diff["stage_identities"]["model_identity"]["equal"] is False
    assert diff["artifact_differences"]

    command = frozen_module_command(
        "package.module",
        ["run", "--dataset-ref", "latest", "--output", "old"],
        dataset_version_id="dv-fixed",
        output_flag="--output",
    )
    assert command["command"][-4:] == ["--dataset-ref", "dv-fixed", "--output", "{output}"]
    appended = frozen_module_command(
        "package.module", ["run"], dataset_version_id="dv-fixed", output_flag="--output"
    )
    assert appended["command"][-4:] == ["--dataset-ref", "dv-fixed", "--output", "{output}"]
    with pytest.raises(ValueError, match="missing its value"):
        run_manifest._replace_argument(["run", "--dataset-ref"], "--dataset-ref", "dv-fixed")


def _archive_replayable(settings: Settings, tmp_path: Path, artifact: Path | None = None) -> dict[str, Any]:
    artifacts = [artifact_record(artifact, role="prediction")] if artifact is not None else []
    manifest = build_run_manifest(
        source_kind="replay-test",
        status="SUCCEEDED",
        components=_components(),
        artifacts=artifacts,
        reproduction={
            "command": ["{python}", "-c", "print('replayed')"],
            "working_directory": "{repository}",
        },
    )
    write_run_manifest(manifest, local_root=tmp_path / "local-run", store=store_root(settings))
    return manifest


def test_reproduction_executes_compares_and_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    manifest = _archive_replayable(settings, tmp_path)

    def successful_run(command, cwd, capture_output, text, check):
        assert command[0] == sys.executable
        assert capture_output is True and text is True and check is False
        output = settings.paths.output / "reproductions" / manifest["run_id"]
        output.mkdir(parents=True, exist_ok=True)
        (output / run_manifest.RUN_MANIFEST_FILE).write_text(json.dumps(manifest), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(run_manifest.subprocess, "run", successful_run)
    execution = execute_reproduction(settings, manifest)
    assert execution["status"] == "EXECUTED"
    assert execution["diff"]["equivalent"] is True
    result = reproduce_run(settings, manifest["run_id"], execute=True)
    assert result["mode"] == "execute"
    assert result["execution"]["status"] == "EXECUTED"

    monkeypatch.setattr(
        run_manifest.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 9, "", "boom"),
    )
    failed = execute_reproduction(settings, manifest)
    assert failed["status"] == "EXECUTION_FAILED"
    assert failed["exit_code"] == 9

    no_contract = build_run_manifest(source_kind="none", status="FAILED", components=_components())
    write_run_manifest(no_contract, local_root=tmp_path / "none", store=store_root(settings))
    with pytest.raises(ValueError, match="no executable reproduction command"):
        execute_reproduction(settings, no_contract)

    invalid_contract = build_run_manifest(
        source_kind="bad-contract",
        status="FAILED",
        components=_components(),
        reproduction={"command": "not-a-list"},
    )
    write_run_manifest(invalid_contract, local_root=tmp_path / "bad-contract", store=store_root(settings))
    with pytest.raises(ValueError, match="no executable reproduction command"):
        execute_reproduction(settings, invalid_contract)

    required = tmp_path / "required.bin"
    required.write_bytes(b"original")
    broken = _archive_replayable(settings, tmp_path / "broken-root", required)
    required.unlink()
    verify_failed = execute_reproduction(settings, broken)
    assert verify_failed["status"] == "VERIFY_FAILED"
    assert reproduce_run(settings, broken["run_id"], execute=True)["status"] == "VERIFY_FAILED"


def test_inspect_and_run_cli_status_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = _settings(tmp_path)
    manifest = _archive_replayable(settings, tmp_path)
    inspected = inspect_run(settings, manifest["run_id"])
    assert inspected["verification"]["passed"] is True
    assert inspected["manifest"]["run_id"] == manifest["run_id"]

    monkeypatch.setattr(run_cli.Settings, "load", lambda *args, **kwargs: settings)
    assert run_cli.main(["--config", "ignored", "run", "inspect", manifest["run_id"]]) == 0
    assert manifest["run_id"] in capsys.readouterr().out
    assert run_cli.main(["--config", "ignored", "run", "reproduce", manifest["run_id"], "--verify-only"]) == 0

    monkeypatch.setattr(
        run_cli,
        "reproduce_run",
        lambda *args, **kwargs: {
            "verification": {"passed": False},
            "runtime_context": {"passed": True},
        },
    )
    assert run_cli.main(["--config", "ignored", "run", "reproduce", manifest["run_id"], "--verify-only"]) == 2
    monkeypatch.setattr(
        run_cli,
        "reproduce_run",
        lambda *args, **kwargs: {
            "verification": {"passed": True},
            "runtime_context": {"passed": True},
            "execution": {"status": "EXECUTION_FAILED"},
        },
    )
    assert run_cli.main(["--config", "ignored", "run", "reproduce", manifest["run_id"], "--execute"]) == 2


def test_adapter_helpers_and_quickstart_nested_artifacts(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    assert run_adapters._load_json(tmp_path / "missing.json") == {}
    non_mapping = tmp_path / "list.json"
    non_mapping.write_text("[]", encoding="utf-8")
    assert run_adapters._load_json(non_mapping) == {}
    assert run_adapters._dataset_evidence(None) == ({}, [])
    assert run_adapters._dataset_evidence(str(tmp_path / "missing-dataset")) == ({}, [])

    dataset = tmp_path / "dataset"
    _write_dataset(dataset)
    metadata, dataset_artifacts = run_adapters._dataset_evidence(str(dataset))
    assert metadata["version_id"] == "dv1"
    assert dataset_artifacts[0]["role"] == "dataset"

    child_artifact = tmp_path / "model.pkl"
    child_artifact.write_bytes(b"model")
    child_manifest = tmp_path / "child_manifest.json"
    child_manifest.write_text(
        json.dumps(
            {
                "schemaVersion": "child-v1",
                "runId": "child",
                "status": "SUCCEEDED",
                "universe": {"id": "pit-universe"},
                "labelSpec": {"horizon": 1},
                "marketRuleSet": {"id": "market-v1"},
                "featureSnapshotId": "fs-child",
                "predictionSnapshotId": "ps-child",
                "artifacts": [
                    {
                        "name": "model.pkl",
                        "localPath": str(child_artifact),
                        "artifactType": "MODEL",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    found, payloads = run_adapters._artifact_from_manifest(child_manifest)
    assert len(found) == 2
    assert run_adapters._find_mapping(payloads, ("universe",)) == {"id": "pit-universe"}
    assert run_adapters._find_value(payloads, ("featureSnapshotId",)) == "fs-child"
    assert run_adapters._find_mapping([{"other": 1}], ("universe",)) is None
    assert run_adapters._find_value([{"other": 1}], ("snapshotId",)) is None

    quick = tmp_path / "quick"
    quick.mkdir()
    (quick / "research_matrix.json").write_text("{}", encoding="utf-8")
    (quick / "run_state.json").write_text("{}", encoding="utf-8")
    plan = {
        "schemaVersion": "2.0",
        "researchId": "research-nested",
        "status": "SUCCEEDED",
        "dataset": {"path": str(dataset), "versionId": "dv1", "dataReleaseId": "dr1"},
        "researchSpec": {
            "dataset": {"versionId": "dv1", "dataReleaseId": "dr1"},
            "research": {
                "alphaPacks": ["alpha158_market_v1"],
                "models": [{"name": "lightgbm"}],
                "split": {"train": ["2020", "2022"]},
                "portfolio": {"benchmark": "SH000300", "topn": 30},
            },
        },
        "jobs": [
            {
                "alphaPack": "alpha158_market_v1",
                "configSha256": "cfg",
                "scientificInputHash": "input",
                "parityNotes": ["known deviation"],
                "result": {"manifest": str(child_manifest)},
                "predictionBacktest": {"result": {"manifest": str(child_manifest)}},
            }
        ],
        "observedWarnings": ["warning"],
        "governance": {"publishingAuthorized": False},
    }
    archive = run_adapters.record_quickstart_run(
        settings,
        plan,
        quick,
        argv=["run", "--dataset-ref", "latest", "--output", str(quick)],
    )
    payload = json.loads(archive.read_text(encoding="utf-8"))
    assert payload["feature"]["feature_snapshot_id"] == "fs-child"
    assert payload["prediction"]["snapshot_id"] == "ps-child"
    assert payload["universe"]["id"] == "pit-universe"
    assert payload["market"]["id"] == "market-v1"
    assert "known deviation" in payload["known_deviations"]


def test_official_and_daily_adapters_cover_optional_artifacts_and_regression_children(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    dataset = tmp_path / "dataset"
    _write_dataset(dataset)

    official = tmp_path / "official"
    official.mkdir()
    (official / "plan.json").write_text(
        json.dumps(
            {
                "dataset": {
                    "provider_uri": str(dataset),
                    "dataset_version_id": "dv1",
                    "data_release_id": "dr1",
                },
                "segments": {"train": ["2020", "2022"]},
                "model": {"num_leaves": 31},
            }
        ),
        encoding="utf-8",
    )
    (official / "parity_report.json").write_text(
        json.dumps(
            {
                "status": "PASS",
                "dataset": {
                    "provider_uri": str(dataset),
                    "dataset_version_id": "dv1",
                    "data_release_id": "dr1",
                },
                "environment": {"git": {"commit": "abc", "dirty": False}},
                "workflow": {"frozen_sha256": "wf"},
                "data_semantics": {"universe": {"passed": True}},
                "seeds": [7, 11],
                "engine_parity": {"passed": True},
                "vendor_parity": {"passed": True},
                "golden_checks": {"passed": True},
            }
        ),
        encoding="utf-8",
    )
    (official / "parity_report.md").write_text("# parity\n", encoding="utf-8")
    (official / "runtime_workflow.yaml").write_text("market: csi300\n", encoding="utf-8")
    (official / "extra.txt").write_text("evidence", encoding="utf-8")
    official_archive = run_adapters.record_official_parity_run(
        settings,
        official,
        argv=["run", "--dataset-ref", "latest", "--output-dir", str(official)],
    )
    official_payload = json.loads(official_archive.read_text(encoding="utf-8"))
    assert official_payload["status"] == "PASS"
    assert official_payload["gates"]["engine_parity"]["passed"] is True

    daily = tmp_path / "daily"
    daily.mkdir()
    report = daily / "report.md"
    report.write_text("# report\n", encoding="utf-8")
    plan = daily / "plan.json"
    plan.write_text("{}", encoding="utf-8")
    regression = daily / "regression"
    regression.mkdir()
    (regression / "research_matrix.json").write_text("{}", encoding="utf-8")
    (regression / "run_manifest.json").write_text("{}", encoding="utf-8")
    daily_manifest = daily / "manifest.json"
    daily_manifest.write_text(
        json.dumps(
            {
                "status": "SUCCEEDED",
                "target_session": "20260916",
                "report": str(report),
                "plan": str(plan),
                "immutable_input": {
                    "data_release_id": "dr1",
                    "dataset_version_id": "dv1",
                    "dataset_manifest_sha256": "manifest1",
                },
                "dataset": {"data_path": str(dataset)},
                "lineage": {
                    "target_session": "20260916",
                    "code": {"commit": "abc", "dirty": False},
                    "provider": {"watermarks_at_plan": {"daily": "20260916"}},
                    "universe": {"configuration": {"id": "csi300"}},
                    "benchmark": {"symbol": "SH000300"},
                    "model_policy": {"automatic_selection": False},
                },
                "steps": {
                    "feature_materialization": {"status": "SUCCEEDED"},
                    "regression_backtest": {
                        "status": "SUCCEEDED",
                        "output": {"output_root": str(regression)},
                    },
                },
                "checkpoint_ledger": {"dataset_verify": {"status": "SUCCEEDED"}},
            }
        ),
        encoding="utf-8",
    )
    daily_archive = run_adapters.record_daily_run(settings, daily_manifest, argv=["--resume", "plan-1"])
    daily_payload = json.loads(daily_archive.read_text(encoding="utf-8"))
    assert daily_payload["status"] == "SUCCEEDED"
    assert daily_payload["prediction"]["regression_status"] == "SUCCEEDED"
    assert daily_payload["gates"]["checkpoint_ledger"]["dataset_verify"]["status"] == "SUCCEEDED"


def test_governed_entrypoint_records_manifest_and_restores_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    root = tmp_path / "governed"
    original_writer = governed_entrypoint.legacy._write_matrix
    recorded: list[tuple[Path, dict[str, object], list[str]]] = []

    class Parser:
        def parse_args(self, argv):
            return SimpleNamespace(config="ignored")

    monkeypatch.setattr(governed_entrypoint.governed, "_parser", lambda: Parser())
    monkeypatch.setattr(governed_entrypoint.Settings, "load", lambda *args, **kwargs: settings)
    monkeypatch.setattr(sys, "argv", ["tq-research", "run"])
    monkeypatch.setattr(
        governed_entrypoint,
        "record_quickstart_run",
        lambda current, payload, current_root, argv: recorded.append(
            (current_root, dict(payload), list(argv))
        ),
    )

    def fake_main() -> int:
        governed_entrypoint.legacy._write_matrix(root, {"status": "FAILED"})
        return 7

    monkeypatch.setattr(governed_entrypoint.governed, "main", fake_main)
    assert governed_entrypoint.main() == 7
    assert recorded == [(root, {"status": "FAILED"}, ["run"])]
    assert governed_entrypoint.legacy._write_matrix is original_writer


def test_parity_entrypoint_records_success_and_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    output = tmp_path / "parity-wrapper"
    output.mkdir()
    (output / "plan.json").write_text("{}", encoding="utf-8")
    records: list[str | None] = []

    class Parser:
        def parse_args(self, argv):
            return SimpleNamespace(config="ignored", output_dir=str(output))

    monkeypatch.setattr(parity_entrypoint.official_parity, "_parser", lambda: Parser())
    monkeypatch.setattr(parity_entrypoint.Settings, "load", lambda *args, **kwargs: settings)
    monkeypatch.setattr(sys, "argv", ["tq-official-parity", "run"])
    monkeypatch.setattr(
        parity_entrypoint,
        "record_official_parity_run",
        lambda *args, status=None, **kwargs: records.append(status),
    )
    monkeypatch.setattr(parity_entrypoint.official_parity, "main", lambda: 0)
    assert parity_entrypoint.main() == 0
    assert records == [None]

    records.clear()

    def boom() -> int:
        raise RuntimeError("boom")

    monkeypatch.setattr(parity_entrypoint.official_parity, "main", boom)
    with pytest.raises(RuntimeError, match="boom"):
        parity_entrypoint.main()
    assert records == ["FAILED"]


def test_daily_entrypoint_records_success_failure_and_restores_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    runner = object.__new__(daily_entrypoint.ManifestDailyResearchRun)
    runner.settings = settings
    runner.root = tmp_path / "daily-state"
    manifest_path = tmp_path / "daily-manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    records: list[Path] = []
    monkeypatch.setattr(
        daily_entrypoint,
        "record_daily_run",
        lambda current, path, argv: records.append(Path(path)),
    )
    monkeypatch.setattr(
        daily_entrypoint._ProductionDailyRun,
        "execute_plan",
        lambda self, *args, **kwargs: manifest_path,
    )
    monkeypatch.setattr(sys, "argv", ["tq-daily-run", "--resume", "plan-1"])
    assert runner.execute_plan("plan-1") == manifest_path
    assert records == [manifest_path]

    records.clear()
    failed = runner._manifest_path("plan-failed")
    failed.parent.mkdir(parents=True, exist_ok=True)
    failed.write_text("{}", encoding="utf-8")

    def fail_execute(self, *args, **kwargs):
        raise RuntimeError("failed")

    monkeypatch.setattr(daily_entrypoint._ProductionDailyRun, "execute_plan", fail_execute)
    with pytest.raises(RuntimeError, match="failed"):
        runner.execute_plan("plan-failed")
    assert records == [failed]

    original = daily_entrypoint.production_daily_run.DailyResearchRun
    monkeypatch.setattr(daily_entrypoint.production_daily_run, "main", lambda: 0)
    assert daily_entrypoint.main() == 0
    assert daily_entrypoint.production_daily_run.DailyResearchRun is original


def test_cli_entrypoint_dispatches_run_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["qlib-platform", "run", "inspect", "run-fixture"])
    monkeypatch.setattr(run_cli, "main", lambda argv=None: 0)
    cli_entrypoint.main()

    monkeypatch.setattr(run_cli, "main", lambda argv=None: 2)
    with pytest.raises(SystemExit) as exc:
        cli_entrypoint.main()
    assert exc.value.code == 2
