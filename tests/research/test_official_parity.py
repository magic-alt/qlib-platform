from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest
import yaml

from qlib_platform.datasets.dataset_resolver import ResolvedDataset
from qlib_platform.research.workflow import official_parity as parity
from qlib_platform.runtime.runtime_resources import resource_path
from qlib_platform.settings import Paths, Settings


def _settings(tmp_path: Path) -> Settings:
    paths = Paths.from_root(tmp_path / "data")
    paths.mkdirs()
    config = tmp_path / "configs" / "pipeline.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("mode: standalone\ndata_source:\n  kind: local\n", encoding="utf-8")
    return Settings(
        config_path=config,
        data={
            "mode": "standalone",
            "data_source": {"kind": "local"},
            "qlib": {
                "dataset_dir": str(paths.root / "qlib"),
                "dataset_name": "official-parity-fixture",
                "dataset_version": "fixture",
                "dataset_ref": "fixture-current",
                "include_fields": [],
            },
        },
        paths=paths,
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=paths.root / "qlib",
    )


def _provider(tmp_path: Path, *, version: str = "version-a") -> ResolvedDataset:
    root = tmp_path / version
    (root / "calendars").mkdir(parents=True)
    (root / "instruments").mkdir()
    (root / "features" / "sh000300").mkdir(parents=True)
    (root / "features" / "sh600000").mkdir()
    (root / "calendars" / "day.txt").write_text(
        "2008-01-01\n2015-01-05\n2017-01-03\n2020-08-01\n", encoding="utf-8"
    )
    (root / "instruments" / "csi300.txt").write_text(
        "sh600000\t2008-01-01\t2020-08-01\n"
        "sz000001\t2010-01-04\t2020-08-01\n",
        encoding="utf-8",
    )
    for field in ("open", "high", "low", "close", "volume", "factor"):
        (root / "features" / "sh600000" / f"{field}.day.bin").write_bytes(field.encode())
    (root / "features" / "sh000300" / "close.day.bin").write_bytes(b"benchmark")
    manifest = root / "dataset_manifest.json"
    payload = {
        "schema_version": "3.0",
        "dataset_name": "official-parity-fixture",
        "version_id": version,
        "data_path": str(root),
        "status": "PUBLISHED",
        "data_release_id": "ds_fixture",
        "data_release_manifest_sha256": "d" * 64,
        "staging_manifest_sha256": "b" * 64,
        "universe_membership_sha256": "c" * 64,
        "semantic_contract": {
            "adjustment_policy": "stable_total_return_first_valid_anchor",
            "pit_availability_policy": "next_trading_day",
        },
        "partitions": [
            {
                "path": "calendars/day.txt",
                "partition_key": "calendars/day.txt",
                "bytes": (root / "calendars" / "day.txt").stat().st_size,
                "sha256": "a" * 64,
            }
        ],
    }
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    return ResolvedDataset(
        reference=version,
        version_id=version,
        dataset_name="official-parity-fixture",
        data_path=root,
        manifest_path=manifest,
        manifest_sha256=parity.sha256_file(manifest),
    )


def _golden(tmp_path: Path, *, passed: bool = True) -> Path:
    path = tmp_path / "golden.yaml"
    checks = [
        {"kind": "csi300_rebalance", "passed": passed, "evidence": "2018-06 rebalance checked"},
        {"kind": "csi300_rebalance", "passed": passed, "evidence": "2019-12 rebalance checked"},
        {"kind": "corporate_action", "passed": passed, "evidence": "split/dividend date checked"},
        {"kind": "corporate_action", "passed": passed, "evidence": "second action date checked"},
    ]
    path.write_text(yaml.safe_dump({"checks": checks}, sort_keys=False), encoding="utf-8")
    return path


def _profile() -> tuple[Path, dict[str, Any], Path, dict[str, Any]]:
    return parity.load_profile(resource_path("configs/research/qlib_official_alpha158_lgb_v1.yaml"))


def _lane(metrics: dict[str, float] | None = None) -> dict[str, Any]:
    profile = _profile()[1]
    values = {
        key: float(profile["official_reference"][key])
        for key in parity.METRIC_KEYS
    }
    if metrics:
        values.update(metrics)
    pred = pd.DataFrame({"score": [0.1, 0.2]}, index=["a", "b"])
    label = pd.DataFrame({"label": [0.2, 0.1]}, index=["a", "b"])
    ic = pd.Series([0.04, 0.05], index=pd.to_datetime(["2020-01-02", "2020-01-03"]))
    ric = pd.Series([0.05, 0.04], index=ic.index)
    report = pd.DataFrame(
        {"return": [0.01, -0.01], "bench": [0.0, 0.0], "cost": [0.001, 0.001]},
        index=ic.index,
    )
    positions = pd.Series([{"cash": 1.0}, {"cash": 1.0}], index=ic.index)
    objects = {
        "pred.pkl": pred,
        "label.pkl": label,
        "sig_analysis/ic.pkl": ic,
        "sig_analysis/ric.pkl": ric,
        "portfolio_analysis/report_normal_1day.pkl": report,
        "portfolio_analysis/positions_normal_1day.pkl": positions,
        "portfolio_analysis/port_analysis_1day.pkl": pd.DataFrame({"risk": [1.0]}),
    }
    return {
        "recorder_id": "recorder-fixture",
        "metrics": values,
        "artifact_schema": sorted(objects),
        "objects": objects,
        "artifact_hashes": {
            "predictions": parity._frame_hash(pred),
            "ic_series": parity._frame_hash(ic),
            "rank_ic_series": parity._frame_hash(ric),
            "portfolio_report": parity._frame_hash(report),
        },
    }


def test_frozen_workflow_matches_pinned_upstream_contract() -> None:
    _, profile, workflow_path, workflow = _profile()
    assert parity.sha256_file(workflow_path) == profile["workflow_sha256"]
    assert profile["upstream"]["commit"] == "79633dd9506ea689e5400dea0197717b5b3d74b7"
    parity.validate_official_workflow(workflow)
    assert workflow["task"]["dataset"]["kwargs"]["handler"]["module_path"] == "qlib.contrib.data.handler"
    assert workflow["task"]["model"]["kwargs"] == parity.EXPECTED_MODEL
    assert workflow["port_analysis_config"]["backtest"] == parity.EXPECTED_BACKTEST


def test_workflow_drift_is_rejected() -> None:
    workflow = _profile()[3]
    drifted = json.loads(json.dumps(parity._normalize(workflow)))
    drifted["task"]["model"]["kwargs"]["learning_rate"] = 0.1
    with pytest.raises(ValueError, match="model.kwargs"):
        parity.validate_official_workflow(drifted)

    drifted = json.loads(json.dumps(parity._normalize(workflow)))
    drifted["task"]["record"].pop()
    with pytest.raises(ValueError, match="record"):
        parity.validate_official_workflow(drifted)


def test_profile_hash_seed_and_governance_are_fail_closed(tmp_path: Path) -> None:
    source_profile, profile, source_workflow, _ = _profile()
    workflow = tmp_path / source_workflow.name
    workflow.write_bytes(source_workflow.read_bytes())

    def write_profile(**updates: Any) -> Path:
        payload = json.loads(json.dumps(parity._normalize(profile)))
        payload["workflow_file"] = workflow.name
        payload.update(updates)
        target = tmp_path / "profile.yaml"
        target.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
        return target

    with pytest.raises(ValueError, match="workflow hash mismatch"):
        parity.load_profile(write_profile(workflow_sha256="0" * 64))
    with pytest.raises(ValueError, match="two distinct seeds"):
        parity.load_profile(write_profile(seeds=[7, 7]))
    with pytest.raises(ValueError, match="disable model selection"):
        parity.load_profile(write_profile(automatic_model_selection=True))

    bad = json.loads(json.dumps(parity._normalize(profile)))
    bad["workflow_file"] = workflow.name
    bad["vendor_tolerances"]["ic"]["severity"] = "secondary"
    target = tmp_path / "profile-severity.yaml"
    target.write_text(yaml.safe_dump(bad, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid severity"):
        parity.load_profile(target)
    assert source_profile.is_file()


def test_runtime_workflow_changes_only_provider_uri(tmp_path: Path) -> None:
    workflow = _profile()[3]
    rendered_path = parity.render_runtime_workflow(workflow, tmp_path / "provider", tmp_path / "runtime.yaml")
    rendered = parity._load_yaml(rendered_path)
    original = json.loads(json.dumps(parity._normalize(workflow)))
    original["qlib_init"]["provider_uri"] = str((tmp_path / "provider").resolve())
    assert parity._normalize(rendered) == original
    parity.validate_official_workflow(rendered)


def test_dataset_manifest_requires_v3_release_and_semantics(tmp_path: Path) -> None:
    resolved = _provider(tmp_path)
    payload = parity._dataset_manifest(resolved)
    assert payload["data_release_id"] == "ds_fixture"
    audit = parity.audit_dataset_semantics(resolved, payload)
    assert audit["passed"] is True
    assert {item["name"] for item in audit["checks"]} == {
        "trading_calendar",
        "csi300_point_in_time_intervals",
        "benchmark_sh000300",
        "ohlcv_factor_fields",
        "frozen_release_lineage",
        "content_addressed_partitions",
    }

    payload.pop("data_release_id")
    resolved.manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="frozen DataRelease"):
        parity._dataset_manifest(resolved)


def test_dataset_semantic_failures_are_explicit(tmp_path: Path) -> None:
    resolved = _provider(tmp_path)
    manifest = json.loads(resolved.manifest_path.read_text(encoding="utf-8"))
    (resolved.data_path / "instruments" / "csi300.txt").write_text("badline\n", encoding="utf-8")
    (resolved.data_path / "features" / "sh000300" / "close.day.bin").unlink()
    manifest["data_release_manifest_sha256"] = "short"
    manifest["partitions"] = []
    audit = parity.audit_dataset_semantics(resolved, manifest)
    assert audit["passed"] is False
    failures = {item["name"] for item in audit["checks"] if not item["passed"]}
    assert {
        "csi300_point_in_time_intervals",
        "benchmark_sh000300",
        "ohlcv_factor_fields",
        "frozen_release_lineage",
        "content_addressed_partitions",
    }.issubset(failures)


def test_golden_checks_require_two_rebalances_and_two_actions(tmp_path: Path) -> None:
    assert parity.validate_golden_checks(None)["status"] == "MISSING"
    good = parity.validate_golden_checks(_golden(tmp_path))
    assert good["passed"] is True
    bad = parity.validate_golden_checks(_golden(tmp_path, passed=False))
    assert bad["status"] == "FAIL"

    malformed = tmp_path / "malformed.yaml"
    malformed.write_text(
        yaml.safe_dump({"checks": [{"kind": "unknown", "passed": True, "evidence": "x"}]}),
        encoding="utf-8",
    )
    assert parity.validate_golden_checks(malformed)["passed"] is False


def test_build_plan_identity_is_path_independent_and_dataset_sensitive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    resolved = _provider(tmp_path)
    monkeypatch.setattr(parity, "resolve_dataset", lambda *args, **kwargs: resolved)
    golden = _golden(tmp_path)
    profile = resource_path("configs/research/qlib_official_alpha158_lgb_v1.yaml")
    first = parity.build_plan(
        settings,
        dataset_ref="version-a",
        profile_path=profile,
        output_dir=tmp_path / "one",
        golden_checks=golden,
    )[0]
    second = parity.build_plan(
        settings,
        dataset_ref="version-a",
        profile_path=profile,
        output_dir=tmp_path / "two",
        golden_checks=golden,
    )[0]
    assert first["scientific_identity"] == second["scientific_identity"]
    assert first["automatic_model_selection"] is False
    assert first["automatic_promotion"] is False

    changed = _provider(tmp_path, version="version-b")
    monkeypatch.setattr(parity, "resolve_dataset", lambda *args, **kwargs: changed)
    third = parity.build_plan(
        settings,
        dataset_ref="version-b",
        profile_path=profile,
        output_dir=tmp_path / "three",
        golden_checks=golden,
    )[0]
    assert third["scientific_identity"] != first["scientific_identity"]


def test_numeric_frame_delta_and_engine_parity() -> None:
    profile = _profile()[1]
    native = _lane()
    platform = _lane()
    report = parity.compare_engine_parity(native, platform, profile)
    assert report["passed"] is True
    assert report["prediction_max_abs"] == 0.0
    assert report["portfolio_max_abs"] == 0.0

    platform["metrics"]["ic"] += 0.1
    platform["objects"]["pred.pkl"].iloc[0, 0] += 0.1
    report = parity.compare_engine_parity(native, platform, profile)
    assert report["passed"] is False
    assert report["metric_max_abs"] > 0
    assert report["prediction_max_abs"] > 0
    assert parity._numeric_frame_delta(pd.DataFrame({"x": [1]}), pd.DataFrame({"y": [1]})) == float("inf")


def test_vendor_parity_pass_and_failure_attributions(tmp_path: Path) -> None:
    profile = _profile()[1]
    resolved = _provider(tmp_path)
    data_audit = parity.audit_dataset_semantics(
        resolved, json.loads(resolved.manifest_path.read_text(encoding="utf-8"))
    )
    golden = parity.validate_golden_checks(_golden(tmp_path))
    passed = parity.evaluate_vendor_parity([_lane(), _lane()], profile, data_audit=data_audit, golden=golden)
    assert passed["status"] == "PASS"
    assert all(row["attribution"]["category"] is None for row in passed["metrics"])

    failed = parity.evaluate_vendor_parity(
        [_lane({"ic": 0.2, "icir": 1.0, "annualized_return": 0.5}), _lane({"ic": 0.2, "icir": 1.0, "annualized_return": 0.5})],
        profile,
        data_audit=data_audit,
        golden=golden,
    )
    categories = {row["metric"]: row["attribution"]["category"] for row in failed["metrics"] if not row["passed"]}
    assert categories["ic"] == "feature"
    assert categories["icir"] == "model"
    assert categories["annualized_return"] == "backtest"
    assert failed["automatic_parameter_changes"] is False

    universe_audit = {"passed": False, "checks": [{"passed": False, "category": "universe"}]}
    universe = parity.evaluate_vendor_parity(
        [_lane({"ic": 0.2}), _lane({"ic": 0.2})],
        profile,
        data_audit=universe_audit,
        golden=golden,
    )
    assert next(row for row in universe["metrics"] if row["metric"] == "ic")["attribution"]["category"] == "universe"

    with pytest.raises(ValueError, match="at least two seeds"):
        parity.evaluate_vendor_parity([_lane()], profile, data_audit=data_audit, golden=golden)


def test_collect_recorder_requires_official_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    lane = _lane()

    class Recorder:
        id = "recorder-id"

        def list_metrics(self):
            return {parity.METRIC_KEYS[name]: value for name, value in lane["metrics"].items()}

        def load_object(self, name: str):
            return lane["objects"][name]

    monkeypatch.setattr("qlib.workflow.R.list_recorders", lambda experiment_name: {"x": Recorder()})
    result = parity._collect_recorder("fixture")
    assert result["recorder_id"] == "recorder-id"
    assert result["artifact_schema"] == sorted(parity.EXPECTED_ARTIFACTS)

    monkeypatch.setattr("qlib.workflow.R.list_recorders", lambda experiment_name: {})
    with pytest.raises(RuntimeError, match="expected one recorder"):
        parity._collect_recorder("fixture")


def test_run_lane_delegates_native_and_platform(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[str] = []
    monkeypatch.setattr(parity, "_collect_recorder", lambda experiment: _lane())
    monkeypatch.setattr("qlib.cli.run.workflow", lambda *args, **kwargs: calls.append("native"))
    monkeypatch.setattr(
        "qlib_platform.qlib_compat.workflow.run_qrun",
        lambda *args, **kwargs: calls.append("platform"),
    )
    workflow = resource_path("configs/research/qlib_official_alpha158_lgb_v1.workflow.yaml")
    assert parity.run_lane(workflow, lane="native", seed=0, lane_root=tmp_path / "n")["lane"] == "native"
    assert parity.run_lane(workflow, lane="platform", seed=1, lane_root=tmp_path / "p")["seed"] == 1
    assert calls == ["native", "platform"]
    with pytest.raises(ValueError, match="unsupported parity lane"):
        parity.run_lane(workflow, lane="bad", seed=2, lane_root=tmp_path / "bad")


def test_run_official_parity_writes_pass_report_without_live_vendor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    resolved = _provider(tmp_path)
    golden = _golden(tmp_path)
    monkeypatch.setattr(parity, "resolve_dataset", lambda *args, **kwargs: resolved)

    def verify(*args: Any, evidence: dict[str, object] | None = None, **kwargs: Any):
        if evidence is not None:
            evidence.update({"mode": "sampled", "verified": True})
        return {"data_release_id": "ds_fixture"}

    monkeypatch.setattr(parity, "verify_dataset_manifest", verify)
    calls: list[tuple[str, int]] = []

    def runner(workflow: Path, *, lane: str, seed: int, lane_root: Path):
        assert workflow.is_file()
        assert lane_root.is_absolute()
        calls.append((lane, seed))
        return {**_lane(), "lane": lane, "seed": seed}

    report_path = parity.run_official_parity(
        settings,
        dataset_ref="version-a",
        profile_path=resource_path("configs/research/qlib_official_alpha158_lgb_v1.yaml"),
        output_dir=tmp_path / "output",
        golden_checks=golden,
        lane_runner=runner,
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "PASS"
    assert calls == [("native", 0), ("platform", 0), ("platform", 1)]
    assert report["engine_parity"]["passed"] is True
    assert report["vendor_parity"]["seed_count"] == 2
    assert report["automatic_model_selection"] is False
    assert report["automatic_promotion"] is False
    assert (tmp_path / "output" / "parity_report.md").is_file()
    runtime = parity._load_yaml(tmp_path / "output" / "runtime_workflow.yaml")
    assert runtime["qlib_init"]["provider_uri"] == str(resolved.data_path.resolve())


def test_run_official_parity_keeps_failed_vendor_result_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    resolved = _provider(tmp_path)
    monkeypatch.setattr(parity, "resolve_dataset", lambda *args, **kwargs: resolved)
    monkeypatch.setattr(parity, "verify_dataset_manifest", lambda *args, **kwargs: {})

    def runner(workflow: Path, *, lane: str, seed: int, lane_root: Path):
        metrics = None if lane == "native" else {"ic": 0.5}
        return {**_lane(metrics), "lane": lane, "seed": seed}

    report_path = parity.run_official_parity(
        settings,
        dataset_ref="version-a",
        profile_path=resource_path("configs/research/qlib_official_alpha158_lgb_v1.yaml"),
        output_dir=tmp_path / "fail-output",
        golden_checks=_golden(tmp_path),
        lane_runner=runner,
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "FAIL"
    assert report["engine_parity"]["passed"] is False
    assert report["vendor_parity"]["passed"] is False
    assert report["vendor_parity"]["automatic_parameter_changes"] is False


def test_main_plan_and_failed_run_exit_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    plan_payload = {"scientific_identity": "science-id"}
    monkeypatch.setattr(parity.Settings, "load", lambda *args, **kwargs: settings)
    monkeypatch.setattr(parity, "build_plan", lambda *args, **kwargs: (plan_payload, {}, {}))
    out = tmp_path / "cli-plan"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tq-official-parity",
            "plan",
            "--dataset-ref",
            "ds",
            "--output-dir",
            str(out),
        ],
    )
    assert parity.main() == 0
    assert json.loads((out / "plan.json").read_text(encoding="utf-8"))["scientific_identity"] == "science-id"

    failed = tmp_path / "failed.json"
    failed.write_text(json.dumps({"status": "FAIL"}), encoding="utf-8")
    monkeypatch.setattr(parity, "run_official_parity", lambda *args, **kwargs: failed)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tq-official-parity",
            "run",
            "--dataset-ref",
            "ds",
            "--output-dir",
            str(tmp_path / "cli-run"),
        ],
    )
    assert parity.main() == 2
