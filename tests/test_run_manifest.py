from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from qlib_platform.research.evidence.run_adapters import (
    record_daily_run,
    record_official_parity_run,
    record_quickstart_run,
)
from qlib_platform.research.evidence.run_manifest import (
    RUN_MANIFEST_SCHEMA,
    artifact_record,
    build_run_manifest,
    config_identity,
    derive_stage_identities,
    resolve_run_manifest,
    reproduce_run,
    store_root,
    verify_run_manifest,
    write_run_manifest,
)
from qlib_platform.settings import Paths, Settings


def _settings(tmp_path: Path) -> Settings:
    config = tmp_path / "pipeline.yaml"
    config.write_text("mode: standalone\napi_token: never-persist-this\n", encoding="utf-8")
    paths = Paths.from_root(tmp_path / "data")
    paths.mkdirs()
    return Settings(
        config_path=config,
        data={
            "mode": "standalone",
            "project_root": str(paths.root),
            "api_token": "never-persist-this",
            "nested": {"password": "also-secret"},
            "data_source": {"provider": "fixture"},
            "universe": {"id": "csi300", "pit": True},
            "experiment": {"label": {"horizon": 1, "cutoff": "T"}},
            "market_rules": {"cost": 0.001, "slippage": 0.0002},
        },
        paths=paths,
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=paths.qlib_versions / "dv1",
    )


def _components() -> dict[str, object]:
    return {
        "code": {"commit": "abc", "dirty": False},
        "resolved_config": {"canonical_sha256": "cfg"},
        "environment": {"fingerprint": "env"},
        "data": {"data_release_id": "dr1", "dataset_version_id": "dv1", "calendar": "cn-day-v1"},
        "universe": {"id": "csi300", "pit": True, "membership_sha256": "u1"},
        "feature": {"alpha_pack": "alpha158", "feature_snapshot_id": "fs1", "materialization_sha256": "f1"},
        "label": {"expression": "T+1", "horizon": 1, "cutoff": "T"},
        "split": {"train": ["2018", "2022"], "test": ["2023", "2024"], "purge": 1, "embargo": 1},
        "model": {"class": "LightGBM", "params": {"num_leaves": 31}, "seed": 7, "artifact": "m1"},
        "prediction": {"snapshot_id": "ps1"},
        "portfolio": {"strategy": "TopkDropoutStrategy", "topk": 30},
        "market": {"rule_set": "ashare-v1", "cost": 0.001, "slippage": 0.0002, "fill": "open"},
        "benchmark": {"identity": "SH000300"},
    }


def _assert_changed(before: dict[str, str], after: dict[str, str], keys: set[str]) -> None:
    for key in before:
        assert (before[key] != after[key]) is (key in keys), key


def test_stage_identity_changes_only_from_business_inputs() -> None:
    base = _components()
    identities = derive_stage_identities(base)
    all_stages = set(identities)

    changed = deepcopy(base)
    changed["data"]["data_release_id"] = "dr2"  # type: ignore[index]
    _assert_changed(identities, derive_stage_identities(changed), all_stages)

    changed = deepcopy(base)
    changed["feature"]["feature_snapshot_id"] = "fs2"  # type: ignore[index]
    _assert_changed(
        identities,
        derive_stage_identities(changed),
        {"feature_identity", "model_identity", "prediction_identity", "backtest_identity"},
    )

    changed = deepcopy(base)
    changed["label"]["horizon"] = 5  # type: ignore[index]
    _assert_changed(
        identities,
        derive_stage_identities(changed),
        {"model_identity", "prediction_identity", "backtest_identity"},
    )

    changed = deepcopy(base)
    changed["model"]["seed"] = 8  # type: ignore[index]
    _assert_changed(
        identities,
        derive_stage_identities(changed),
        {"model_identity", "prediction_identity", "backtest_identity"},
    )

    changed = deepcopy(base)
    changed["market"]["cost"] = 0.002  # type: ignore[index]
    _assert_changed(identities, derive_stage_identities(changed), {"backtest_identity"})

    changed = deepcopy(base)
    changed["feature"]["notes"] = "presentation only"  # type: ignore[index]
    assert derive_stage_identities(changed) == identities


def test_run_definition_ignores_presentation_but_output_digest_changes_run_id(tmp_path: Path) -> None:
    base = _components()
    first = tmp_path / "predictions.parquet"
    first.write_bytes(b"first")
    one = build_run_manifest(
        source_kind="test",
        status="SUCCEEDED",
        components=base,
        artifacts=[artifact_record(first, role="prediction")],
    )
    presentation = deepcopy(base)
    presentation["feature"]["notes"] = "human note"  # type: ignore[index]
    same = build_run_manifest(
        source_kind="test",
        status="SUCCEEDED",
        components=presentation,
        artifacts=[artifact_record(first, role="prediction")],
    )
    assert same["run_definition_id"] == one["run_definition_id"]
    assert same["run_id"] == one["run_id"]

    first.write_bytes(b"second")
    changed_output = build_run_manifest(
        source_kind="test",
        status="SUCCEEDED",
        components=base,
        artifacts=[artifact_record(first, role="prediction")],
    )
    assert changed_output["run_definition_id"] == one["run_definition_id"]
    assert changed_output["run_id"] != one["run_id"]


def test_config_identity_redacts_secret_values(tmp_path: Path) -> None:
    identity = config_identity(_settings(tmp_path))
    encoded = json.dumps(identity)
    assert "never-persist-this" not in encoded
    assert "also-secret" not in encoded
    assert "config:config.api_token" in identity["secret_references"]
    assert "config:config.nested.password" in identity["secret_references"]


def test_archive_verify_and_tamper_fail_closed(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    artifact = tmp_path / "selection.csv"
    artifact.write_text("symbol,weight\n000001,1\n", encoding="utf-8")
    manifest = build_run_manifest(
        source_kind="test",
        status="SUCCEEDED",
        components=_components(),
        artifacts=[artifact_record(artifact, role="selection", semantic_metadata={"schema": 1})],
    )
    archive = write_run_manifest(manifest, local_root=tmp_path / "run", store=store_root(settings))
    assert verify_run_manifest(archive)["passed"] is True

    with pytest.raises(ValueError, match="immutable run_id"):
        resolve_run_manifest(store_root(settings), "latest")

    artifact.unlink()
    missing = verify_run_manifest(archive)
    assert missing["passed"] is False
    assert missing["errors"][0]["location"].endswith("selection.csv")

    artifact.write_text("symbol,weight\n000002,1\n", encoding="utf-8")
    tampered = verify_run_manifest(archive)
    assert tampered["passed"] is False
    assert tampered["errors"][0]["error"] == "artifact content checksum mismatch"


def test_failed_and_rejected_use_the_same_manifest_schema() -> None:
    failed = build_run_manifest(source_kind="test", status="FAILED", components=_components())
    rejected = build_run_manifest(source_kind="test", status="REJECTED", components=_components())
    assert failed["run_schema_version"] == RUN_MANIFEST_SCHEMA
    assert rejected["run_schema_version"] == RUN_MANIFEST_SCHEMA
    assert set(failed) == set(rejected)


def test_verify_only_never_falls_back_to_latest(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    manifest = build_run_manifest(
        source_kind="test",
        status="FAILED",
        components={
            **_components(),
            "code": {},
            "resolved_config": {},
            "environment": {},
        },
    )
    write_run_manifest(manifest, local_root=tmp_path / "run", store=store_root(settings))
    result = reproduce_run(settings, manifest["run_id"], execute=False)
    assert result["mode"] == "verify-only"
    assert result["verification"]["passed"] is True
    with pytest.raises(ValueError, match="immutable run_id"):
        reproduce_run(settings, "latest", execute=False)


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
                "universe_membership_sha256": "universe1",
            }
        ),
        encoding="utf-8",
    )


def test_quickstart_official_and_daily_use_same_manifest_contract(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    dataset_root = tmp_path / "dataset"
    _write_dataset(dataset_root)

    quick_root = tmp_path / "quick"
    quick_root.mkdir()
    plan = {
        "schemaVersion": "2.0",
        "researchId": "research-fixture",
        "status": "REJECTED",
        "dataset": {"path": str(dataset_root), "versionId": "dv1", "dataReleaseId": "dr1"},
        "researchSpec": {
            "dataset": {"versionId": "dv1", "dataReleaseId": "dr1"},
            "research": {
                "alphaPacks": ["alpha158_market_v1"],
                "models": [{"name": "lightgbm", "profile": {"parameters": {"seed": 7}}}],
                "split": {"train": ["2020", "2023"], "test": ["2024", "2025"]},
                "labelIsolation": {"purgeEmbargoRequired": True},
                "portfolio": {"benchmark": "SH000300", "topn": 30},
            },
        },
        "jobs": [],
        "governance": {"publishingAuthorized": False},
    }
    quick_archive = record_quickstart_run(
        settings,
        plan,
        quick_root,
        argv=["run", "--dataset-ref", "current", "--output", str(quick_root)],
    )

    official_root = tmp_path / "official"
    official_root.mkdir()
    (official_root / "plan.json").write_text(
        json.dumps(
            {
                "dataset": {
                    "provider_uri": str(dataset_root),
                    "dataset_version_id": "dv1",
                    "data_release_id": "dr1",
                }
            }
        ),
        encoding="utf-8",
    )
    (official_root / "parity_report.json").write_text(
        json.dumps(
            {
                "status": "FAIL",
                "dataset": {
                    "provider_uri": str(dataset_root),
                    "dataset_version_id": "dv1",
                    "data_release_id": "dr1",
                },
                "environment": {},
                "seeds": [7, 11],
                "engine_parity": {"passed": False},
                "vendor_parity": {"passed": False},
                "golden_checks": {"passed": True},
            }
        ),
        encoding="utf-8",
    )
    official_archive = record_official_parity_run(
        settings,
        official_root,
        argv=["run", "--dataset-ref", "current", "--output-dir", str(official_root)],
    )

    daily_root = tmp_path / "daily"
    daily_root.mkdir()
    report = daily_root / "report.md"
    report.write_text("# daily\n", encoding="utf-8")
    daily_manifest = daily_root / "manifest.json"
    daily_manifest.write_text(
        json.dumps(
            {
                "status": "FAILED",
                "target_session": "20260916",
                "report": str(report),
                "immutable_input": {
                    "data_release_id": "dr1",
                    "dataset_version_id": "dv1",
                },
                "dataset": {
                    "data_path": str(dataset_root),
                    "dataset_version_id": "dv1",
                    "data_release_id": "dr1",
                },
                "lineage": {
                    "target_session": "20260916",
                    "provider": {"watermarks_at_plan": {"daily": "20260916"}},
                    "universe": {"configuration": {"id": "csi300", "pit": True}},
                    "benchmark": {"symbol": "SH000300"},
                    "model_policy": {"automatic_selection": False},
                },
                "steps": {},
                "checkpoint_ledger": {},
            }
        ),
        encoding="utf-8",
    )
    daily_archive = record_daily_run(settings, daily_manifest, argv=["--resume", "plan-fixture"])

    manifests = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (quick_archive, official_archive, daily_archive)
    ]
    assert {item["run_schema_version"] for item in manifests} == {RUN_MANIFEST_SCHEMA}
    assert {item["data"]["dataset_version_id"] for item in manifests} == {"dv1"}
    assert {item["data"]["data_release_id"] for item in manifests} == {"dr1"}
    assert {item["source_kind"] for item in manifests} == {
        "governed-quickstart",
        "official-parity",
        "production-daily-run",
    }
