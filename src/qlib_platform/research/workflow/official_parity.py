from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import random
import sys
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import pandas as pd
import yaml

from qlib_platform.data.store import sha256_file
from qlib_platform.datasets.dataset_manifest import verify_dataset_manifest
from qlib_platform.datasets.dataset_resolver import ResolvedDataset, resolve_dataset
from qlib_platform.lineage import git_revision
from qlib_platform.runtime.runtime_resources import resource_argument
from qlib_platform.settings import Settings

PROFILE_SCHEMA = "official_parity_profile_v1"
PLAN_SCHEMA = "official_parity_plan_v1"
REPORT_SCHEMA = "official_parity_report_v1"

METRIC_KEYS = {
    "ic": "IC",
    "icir": "ICIR",
    "rank_ic": "Rank IC",
    "rank_icir": "Rank ICIR",
    "annualized_return": "1day.excess_return_with_cost.annualized_return",
    "information_ratio": "1day.excess_return_with_cost.information_ratio",
    "max_drawdown": "1day.excess_return_with_cost.max_drawdown",
}
PRIMARY_METRICS = {"ic", "icir", "rank_ic", "rank_icir"}
EXPECTED_MODEL = {
    "loss": "mse",
    "colsample_bytree": 0.8879,
    "learning_rate": 0.2,
    "subsample": 0.8789,
    "lambda_l1": 205.6999,
    "lambda_l2": 580.9768,
    "max_depth": 8,
    "num_leaves": 210,
    "num_threads": 20,
}
EXPECTED_SEGMENTS = {
    "train": ["2008-01-01", "2014-12-31"],
    "valid": ["2015-01-01", "2016-12-31"],
    "test": ["2017-01-01", "2020-08-01"],
}
EXPECTED_BACKTEST = {
    "start_time": "2017-01-01",
    "end_time": "2020-08-01",
    "account": 100000000,
    "benchmark": "SH000300",
    "exchange_kwargs": {
        "limit_threshold": 0.095,
        "deal_price": "close",
        "open_cost": 0.0005,
        "close_cost": 0.0015,
        "min_cost": 5,
    },
}
EXPECTED_ARTIFACTS = {
    "pred.pkl",
    "label.pkl",
    "sig_analysis/ic.pkl",
    "sig_analysis/ric.pkl",
    "portfolio_analysis/report_normal_1day.pkl",
    "portfolio_analysis/positions_normal_1day.pkl",
    "portfolio_analysis/port_analysis_1day.pkl",
}


def _normalize(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return value


def _identity(value: Any) -> str:
    payload = json.dumps(
        _normalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _write_json(path: Path, payload: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(_normalize(payload), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path


def _load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return value


def _eq(actual: Any, expected: Any, path: str) -> None:
    if _normalize(actual) != _normalize(expected):
        raise ValueError(f"official workflow drift at {path}")


def validate_official_workflow(workflow: Mapping[str, Any]) -> None:
    _eq(workflow.get("market"), "csi300", "market")
    _eq(workflow.get("benchmark"), "SH000300", "benchmark")
    init = workflow.get("qlib_init", {})
    handler_cfg = workflow.get("data_handler_config", {})
    task = workflow.get("task", {})
    port = workflow.get("port_analysis_config", {})
    if not all(isinstance(item, Mapping) for item in (init, handler_cfg, task, port)):
        raise ValueError("official workflow sections must be mappings")
    _eq(init.get("region"), "cn", "qlib_init.region")
    _eq(
        handler_cfg,
        {
            "start_time": "2008-01-01",
            "end_time": "2020-08-01",
            "fit_start_time": "2008-01-01",
            "fit_end_time": "2014-12-31",
            "instruments": "csi300",
        },
        "data_handler_config",
    )
    model = task.get("model", {})
    dataset = task.get("dataset", {})
    if not isinstance(model, Mapping) or not isinstance(dataset, Mapping):
        raise ValueError("official model/dataset must be mappings")
    _eq(
        (model.get("class"), model.get("module_path")),
        ("LGBModel", "qlib.contrib.model.gbdt"),
        "model",
    )
    _eq(model.get("kwargs"), EXPECTED_MODEL, "model.kwargs")
    _eq(
        (dataset.get("class"), dataset.get("module_path")),
        ("DatasetH", "qlib.data.dataset"),
        "dataset",
    )
    dataset_kwargs = dataset.get("kwargs", {})
    if not isinstance(dataset_kwargs, Mapping):
        raise ValueError("official dataset kwargs must be a mapping")
    handler = dataset_kwargs.get("handler", {})
    if not isinstance(handler, Mapping):
        raise ValueError("official handler must be a mapping")
    _eq(
        (handler.get("class"), handler.get("module_path")),
        ("Alpha158", "qlib.contrib.data.handler"),
        "handler",
    )
    _eq(dataset_kwargs.get("segments"), EXPECTED_SEGMENTS, "dataset.segments")
    strategy = port.get("strategy", {})
    if not isinstance(strategy, Mapping):
        raise ValueError("official strategy must be a mapping")
    _eq(
        (strategy.get("class"), strategy.get("module_path"), strategy.get("kwargs")),
        (
            "TopkDropoutStrategy",
            "qlib.contrib.strategy",
            {"signal": "<PRED>", "topk": 50, "n_drop": 5},
        ),
        "strategy",
    )
    _eq(port.get("backtest"), EXPECTED_BACKTEST, "backtest")
    records = task.get("record", [])
    classes = [item.get("class") for item in records if isinstance(item, Mapping)]
    _eq(classes, ["SignalRecord", "SigAnaRecord", "PortAnaRecord"], "record")


def load_profile(path: str | Path) -> tuple[Path, dict[str, Any], Path, dict[str, Any]]:
    profile_path = Path(resource_argument(str(path))).expanduser().resolve()
    profile = _load_yaml(profile_path)
    if profile.get("schema_version") != PROFILE_SCHEMA or profile.get("scope") != "official-parity":
        raise ValueError("invalid official parity profile contract")
    if (
        profile.get("automatic_model_selection") is not False
        or profile.get("automatic_promotion") is not False
    ):
        raise ValueError("official parity must disable model selection and promotion")
    workflow_raw = Path(str(profile.get("workflow_file") or ""))
    workflow_path = (
        workflow_raw.resolve()
        if workflow_raw.is_absolute()
        else (profile_path.parent / workflow_raw).resolve()
    )
    if not workflow_path.is_file():
        raise FileNotFoundError(f"frozen official workflow missing: {workflow_path}")
    if sha256_file(workflow_path) != str(profile.get("workflow_sha256") or ""):
        raise ValueError("frozen official workflow hash mismatch")
    workflow = _load_yaml(workflow_path)
    validate_official_workflow(workflow)

    seeds = profile.get("seeds", [])
    if not isinstance(seeds, list) or len({int(value) for value in seeds}) < 2:
        raise ValueError("official parity requires at least two distinct seeds")
    references = profile.get("official_reference", {})
    rules = profile.get("vendor_tolerances", {})
    engine = profile.get("engine_tolerances", {})
    if not all(isinstance(item, Mapping) for item in (references, rules, engine)):
        raise ValueError("parity references/tolerances must be mappings")
    for metric in METRIC_KEYS:
        rule = rules.get(metric)
        if metric not in references or not isinstance(rule, Mapping):
            raise ValueError(f"missing preregistered vendor contract for {metric}")
        if float(rule.get("absolute", 0)) <= 0:
            raise ValueError(f"invalid vendor tolerance for {metric}")
        severity = "primary" if metric in PRIMARY_METRICS else "secondary"
        if rule.get("severity") != severity:
            raise ValueError(f"invalid severity for {metric}: expected {severity}")
    for key in ("metric_max_abs", "prediction_max_abs", "portfolio_max_abs"):
        if float(engine.get(key, -1)) < 0:
            raise ValueError(f"invalid engine tolerance: {key}")
    return profile_path, profile, workflow_path, workflow


def render_runtime_workflow(workflow: Mapping[str, Any], provider_uri: Path, destination: Path) -> Path:
    validate_official_workflow(workflow)
    rendered = json.loads(json.dumps(_normalize(workflow)))
    rendered["qlib_init"]["provider_uri"] = str(provider_uri.resolve())
    validate_official_workflow(rendered)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(yaml.safe_dump(rendered, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return destination


def _dataset_manifest(resolved: ResolvedDataset) -> dict[str, Any]:
    if not resolved.manifest_path.is_file():
        raise FileNotFoundError("official parity requires an immutable DatasetVersion manifest")
    value = json.loads(resolved.manifest_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != "3.0":
        raise ValueError("official parity requires DatasetVersion manifest schema 3.0")
    if not value.get("data_release_id"):
        raise ValueError("official parity DatasetVersion must be bound to a frozen DataRelease")
    return value


def _casefold_child(root: Path, name: str) -> Path | None:
    if not root.is_dir():
        return None
    return next((child for child in root.iterdir() if child.name.casefold() == name.casefold()), None)


def _provider_fields(path: Path | None) -> set[str]:
    if path is None or not path.is_dir():
        return set()
    return {
        item.name.split(".", 1)[0].lower()
        for item in path.iterdir()
        if item.is_file() and item.name.endswith(".day.bin")
    }


def audit_dataset_semantics(resolved: ResolvedDataset, manifest: Mapping[str, Any]) -> dict[str, Any]:
    root = resolved.data_path
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, category: str, **evidence: Any) -> None:
        checks.append({"name": name, "passed": bool(passed), "category": category, "evidence": evidence})

    calendar_path = root / "calendars" / "day.txt"
    dates = (
        [line.strip() for line in calendar_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if calendar_path.is_file()
        else []
    )
    add(
        "trading_calendar",
        bool(dates and dates[0] <= "2008-01-01" and dates[-1] >= "2020-08-01"),
        "data",
        start=dates[0] if dates else None,
        end=dates[-1] if dates else None,
        count=len(dates),
    )

    universe_path = root / "instruments" / "csi300.txt"
    lines = (
        [line.strip() for line in universe_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if universe_path.is_file()
        else []
    )
    intervals = [parts for line in lines if len(parts := line.split()) >= 3]
    interval_ok = (
        bool(intervals)
        and len(intervals) == len(lines)
        and all(parts[0].lower().startswith(("sh", "sz")) and parts[1] <= parts[2] for parts in intervals)
    )
    add(
        "csi300_point_in_time_intervals",
        interval_ok,
        "universe",
        intervals=len(intervals),
        symbols=len({parts[0] for parts in intervals}),
        sha256=sha256_file(universe_path) if universe_path.is_file() else None,
    )

    benchmark = _casefold_child(root / "features", "sh000300")
    benchmark_fields = _provider_fields(benchmark)
    add(
        "benchmark_sh000300",
        "close" in benchmark_fields,
        "data",
        path=str(benchmark or ""),
        available=sorted(benchmark_fields),
    )

    sample_symbol = intervals[0][0] if intervals else ""
    sample = _casefold_child(root / "features", sample_symbol) if sample_symbol else None
    available = _provider_fields(sample)
    required = {"open", "high", "low", "close", "volume", "money", "vwap", "factor"}
    add(
        "ohlcv_factor_fields",
        required.issubset(available),
        "data",
        sample_symbol=sample_symbol or None,
        required=sorted(required),
        available=sorted(available),
    )

    semantic = manifest.get("semantic_contract", {})
    semantic = semantic if isinstance(semantic, Mapping) else {}
    release_sha = str(manifest.get("data_release_manifest_sha256") or "")
    add(
        "frozen_release_lineage",
        bool(manifest.get("data_release_id") and len(release_sha) == 64),
        "data",
        data_release_id=manifest.get("data_release_id"),
        data_release_manifest_sha256=release_sha or None,
        dataset_version_id=resolved.version_id,
        dataset_manifest_sha256=resolved.manifest_sha256,
        staging_manifest_sha256=manifest.get("staging_manifest_sha256"),
        universe_membership_sha256=manifest.get("universe_membership_sha256"),
        adjustment_policy=semantic.get("adjustment_policy"),
        pit_availability_policy=semantic.get("pit_availability_policy"),
    )

    partitions = manifest.get("partitions", [])
    partitions = partitions if isinstance(partitions, list) else []
    valid_partitions = bool(partitions) and all(
        isinstance(item, Mapping)
        and len(str(item.get("sha256") or "")) == 64
        and int(item.get("bytes") or 0) >= 0
        for item in partitions
    )
    add(
        "content_addressed_partitions",
        valid_partitions,
        "data",
        partition_count=len(partitions),
        total_bytes=sum(int(item.get("bytes") or 0) for item in partitions if isinstance(item, Mapping)),
    )
    return {"passed": all(item["passed"] for item in checks), "checks": checks}


def validate_golden_checks(path: str | Path | None) -> dict[str, Any]:
    required = {"csi300_rebalance": 2, "corporate_action": 2}
    if not path:
        return {
            "passed": False,
            "status": "MISSING",
            "required": required,
            "counts": {},
            "checks": [],
        }
    raw = _load_yaml(Path(path).expanduser().resolve()).get("checks", [])
    checks = raw if isinstance(raw, list) else []
    counts = {key: 0 for key in required}
    valid = True
    for item in checks:
        if not isinstance(item, Mapping) or item.get("kind") not in required:
            valid = False
            continue
        if item.get("passed") is True and str(item.get("evidence") or "").strip():
            counts[str(item["kind"])] += 1
    passed = valid and all(counts[key] >= minimum for key, minimum in required.items())
    return {
        "passed": passed,
        "status": "PASS" if passed else "FAIL",
        "required": required,
        "counts": counts,
        "checks": [dict(item) for item in checks if isinstance(item, Mapping)],
    }


def build_plan(
    settings: Settings,
    *,
    dataset_ref: str,
    profile_path: str | Path,
    output_dir: str | Path,
    golden_checks: str | Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    resolved = resolve_dataset(settings, dataset_ref, allow_legacy=False)
    manifest = _dataset_manifest(resolved)
    profile_file, profile, workflow_file, workflow = load_profile(profile_path)
    data_audit = audit_dataset_semantics(resolved, manifest)
    golden = validate_golden_checks(golden_checks)
    plan: dict[str, Any] = {
        "schema_version": PLAN_SCHEMA,
        "profile_id": profile["profile_id"],
        "scope": "official-parity",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": {
            "requested_reference": dataset_ref,
            "dataset_version_id": resolved.version_id,
            "data_release_id": manifest["data_release_id"],
            "dataset_manifest_sha256": resolved.manifest_sha256,
            "data_release_manifest_sha256": manifest.get("data_release_manifest_sha256"),
            "provider_uri": str(resolved.data_path),
        },
        "profile": {
            "path": str(profile_file),
            "sha256": sha256_file(profile_file),
            "workflow_path": str(workflow_file),
            "workflow_sha256": sha256_file(workflow_file),
            "upstream": dict(profile.get("upstream") or {}),
            "seeds": [int(value) for value in profile["seeds"]],
            "engine_tolerances": dict(profile["engine_tolerances"]),
            "vendor_tolerances": dict(profile["vendor_tolerances"]),
        },
        "data_semantics": data_audit,
        "golden_checks": golden,
        "output_dir": str(Path(output_dir).expanduser().resolve()),
        "automatic_model_selection": False,
        "automatic_promotion": False,
    }
    plan["scientific_identity"] = _identity(
        {
            "profile_id": plan["profile_id"],
            "workflow_sha256": plan["profile"]["workflow_sha256"],
            "dataset_version_id": plan["dataset"]["dataset_version_id"],
            "dataset_manifest_sha256": plan["dataset"]["dataset_manifest_sha256"],
            "seeds": plan["profile"]["seeds"],
            "engine_tolerances": plan["profile"]["engine_tolerances"],
            "vendor_tolerances": plan["profile"]["vendor_tolerances"],
        }
    )
    return plan, profile, workflow


@contextmanager
def _cwd(path: Path):
    previous = Path.cwd()
    path.mkdir(parents=True, exist_ok=True)
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _frame_hash(value: Any) -> str:
    frame = value.to_frame("value") if isinstance(value, pd.Series) else value
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("parity artifacts must be pandas Series/DataFrame")
    normalized = frame.sort_index().reindex(sorted(frame.columns), axis=1)
    return hashlib.sha256(pd.util.hash_pandas_object(normalized, index=True).values.tobytes()).hexdigest()


def _collect_recorder(experiment_name: str) -> dict[str, Any]:
    from qlib.utils.exceptions import LoadObjectError
    from qlib.workflow import R

    recorders = R.list_recorders(experiment_name=experiment_name)
    if len(recorders) != 1:
        raise RuntimeError(f"expected one recorder in {experiment_name}, got {len(recorders)}")
    recorder = next(iter(recorders.values()))
    raw_metrics = {str(key): float(value) for key, value in recorder.list_metrics().items()}
    missing_metrics = [key for key in METRIC_KEYS.values() if key not in raw_metrics]
    if missing_metrics:
        raise RuntimeError(f"official recorder missing metrics: {missing_metrics}")

    objects: dict[str, Any] = {}
    schema: set[str] = set()
    for artifact in EXPECTED_ARTIFACTS:
        try:
            objects[artifact] = recorder.load_object(artifact)
            schema.add(artifact)
        except LoadObjectError:
            pass
    missing_artifacts = EXPECTED_ARTIFACTS - schema
    if missing_artifacts:
        raise RuntimeError(f"official recorder missing artifacts: {sorted(missing_artifacts)}")
    return {
        "recorder_id": str(recorder.id),
        "metrics": {name: raw_metrics[key] for name, key in METRIC_KEYS.items()},
        "artifact_schema": sorted(schema),
        "objects": objects,
        "artifact_hashes": {
            "predictions": _frame_hash(objects["pred.pkl"]),
            "ic_series": _frame_hash(objects["sig_analysis/ic.pkl"]),
            "rank_ic_series": _frame_hash(objects["sig_analysis/ric.pkl"]),
            "portfolio_report": _frame_hash(objects["portfolio_analysis/report_normal_1day.pkl"]),
        },
    }


def run_lane(
    runtime_workflow: Path,
    *,
    lane: str,
    seed: int,
    lane_root: Path,
) -> dict[str, Any]:
    random.seed(seed)
    np.random.seed(seed)
    experiment = f"official-parity-{lane}-seed-{seed}"
    with _cwd(lane_root):
        if lane == "native":
            from qlib.cli.run import workflow

            workflow(str(runtime_workflow), experiment_name=experiment, uri_folder="mlruns")
        elif lane == "platform":
            from qlib_platform.qlib_compat.workflow import run_qrun

            run_qrun(str(runtime_workflow), experiment_name=experiment, uri_folder="mlruns")
        else:
            raise ValueError(f"unsupported parity lane: {lane}")
        result = _collect_recorder(experiment)
    result.update({"lane": lane, "seed": seed})
    return result


def _numeric_frame_delta(left: Any, right: Any) -> float:
    left_df = left.to_frame("value") if isinstance(left, pd.Series) else left
    right_df = right.to_frame("value") if isinstance(right, pd.Series) else right
    if not isinstance(left_df, pd.DataFrame) or not isinstance(right_df, pd.DataFrame):
        return float("inf")
    if not left_df.index.equals(right_df.index) or list(left_df.columns) != list(right_df.columns):
        return float("inf")
    left_num = left_df.select_dtypes(include=[np.number])
    right_num = right_df.select_dtypes(include=[np.number])
    if list(left_num.columns) != list(right_num.columns):
        return float("inf")
    if left_num.empty:
        return 0.0 if left_df.equals(right_df) else float("inf")
    values = (left_num.astype(float) - right_num.astype(float)).abs().to_numpy()
    return float(values.max()) if values.size else 0.0


def compare_engine_parity(
    native: Mapping[str, Any],
    platform: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    limits = profile["engine_tolerances"]
    metric_deltas = {
        metric: abs(float(native["metrics"][metric]) - float(platform["metrics"][metric]))
        for metric in METRIC_KEYS
    }
    native_objects = native["objects"]
    platform_objects = platform["objects"]
    prediction_delta = _numeric_frame_delta(native_objects["pred.pkl"], platform_objects["pred.pkl"])
    portfolio_delta = _numeric_frame_delta(
        native_objects["portfolio_analysis/report_normal_1day.pkl"],
        platform_objects["portfolio_analysis/report_normal_1day.pkl"],
    )
    schema_equal = native["artifact_schema"] == platform["artifact_schema"]
    passed = (
        max(metric_deltas.values()) <= float(limits["metric_max_abs"])
        and prediction_delta <= float(limits["prediction_max_abs"])
        and portfolio_delta <= float(limits["portfolio_max_abs"])
        and schema_equal
    )
    return {
        "passed": passed,
        "metric_deltas": metric_deltas,
        "metric_max_abs": max(metric_deltas.values()),
        "prediction_max_abs": prediction_delta,
        "portfolio_max_abs": portfolio_delta,
        "recorder_schema_equal": schema_equal,
        "tolerance": dict(limits),
    }


def _attribution(metric: str, data_audit: Mapping[str, Any], golden: Mapping[str, Any]) -> dict[str, Any]:
    failures = [
        item
        for item in data_audit.get("checks", [])
        if isinstance(item, Mapping) and item.get("passed") is not True
    ]
    categories = {str(item.get("category") or "") for item in failures}
    if "universe" in categories:
        return {"category": "universe", "evidence": "PIT CSI300 interval audit failed"}
    if "data" in categories:
        return {"category": "data", "evidence": "provider semantic/lineage audit failed"}
    if golden.get("passed") is not True:
        return {
            "category": "data",
            "evidence": "manual universe/corporate-action golden checks failed",
        }
    if metric in {"ic", "rank_ic"}:
        return {"category": "feature", "evidence": "inspect Alpha158 feature/label values first"}
    if metric in {"icir", "rank_icir"}:
        return {
            "category": "model",
            "evidence": "inspect model/data interaction and repetition stability",
        }
    return {
        "category": "backtest",
        "evidence": "inspect price, limits, costs, benchmark and tradeability",
    }


def evaluate_vendor_parity(
    seed_results: Sequence[Mapping[str, Any]],
    profile: Mapping[str, Any],
    *,
    data_audit: Mapping[str, Any],
    golden: Mapping[str, Any],
) -> dict[str, Any]:
    if len(seed_results) < 2:
        raise ValueError("vendor parity requires at least two seeds")
    rows = []
    for metric in METRIC_KEYS:
        values = [float(item["metrics"][metric]) for item in seed_results]
        local = float(np.mean(values))
        reference = float(profile["official_reference"][metric])
        rule = profile["vendor_tolerances"][metric]
        delta = local - reference
        passed = abs(delta) <= float(rule["absolute"])
        rows.append(
            {
                "metric": metric,
                "severity": rule["severity"],
                "official_reference": reference,
                "local_mean": local,
                "local_std": float(np.std(values, ddof=1)),
                "delta": delta,
                "absolute_tolerance": float(rule["absolute"]),
                "passed": passed,
                "seed_values": values,
                "attribution": (
                    {"category": None, "evidence": "within preregistered tolerance"}
                    if passed
                    else _attribution(metric, data_audit, golden)
                ),
            }
        )
    passed = (
        all(row["passed"] for row in rows)
        and data_audit.get("passed") is True
        and golden.get("passed") is True
    )
    return {
        "passed": passed,
        "status": "PASS" if passed else "FAIL",
        "seed_count": len(seed_results),
        "metrics": rows,
        "automatic_parameter_changes": False,
    }


def _serializable_lane(result: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in result.items() if key != "objects"}


def _package_versions() -> dict[str, str]:
    versions = {}
    for package in ("qlib-platform", "pyqlib", "lightgbm", "numpy", "pandas", "tushare"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not-installed"
    return versions


def _write_markdown(report: Mapping[str, Any], path: Path) -> Path:
    lines = [
        "# Qlib Official Alpha158 / LightGBM Parity Report",
        "",
        f"- Status: **{report['status']}**",
        f"- Profile: `{report['profile_id']}`",
        f"- Scientific identity: `{report['scientific_identity']}`",
        f"- DatasetVersion: `{report['dataset']['dataset_version_id']}`",
        f"- DataRelease: `{report['dataset']['data_release_id']}`",
        f"- Engine parity: **{'PASS' if report['engine_parity']['passed'] else 'FAIL'}**",
        f"- Data semantics: **{'PASS' if report['data_semantics']['passed'] else 'FAIL'}**",
        f"- Golden checks: **{report['golden_checks']['status']}**",
        "",
        "## Vendor parity",
        "",
        "| Metric | Tier | Official | Local | Delta | Tol. | Result | Attribution |",
        "|---|---|---:|---:|---:|---:|---|---|",
    ]
    for row in report["vendor_parity"]["metrics"]:
        category = row["attribution"]["category"] or "-"
        lines.append(
            f"| {row['metric']} | {row['severity']} | {row['official_reference']:.6f} | "
            f"{row['local_mean']:.6f} | {row['delta']:.6f} | {row['absolute_tolerance']:.6f} | "
            f"{'PASS' if row['passed'] else 'FAIL'} | {category} |"
        )
    lines.extend(
        [
            "",
            "## Governance",
            "",
            "- Frozen upstream workflow hash is verified before execution.",
            "- Runtime mutation is limited to `qlib_init.provider_uri`.",
            "- Failed parity remains FAIL; parameters are never retuned automatically.",
            "- Automatic model selection and promotion are disabled.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_official_parity(
    settings: Settings,
    *,
    dataset_ref: str,
    profile_path: str | Path,
    output_dir: str | Path,
    golden_checks: str | Path | None,
    lane_runner: Callable[..., dict[str, Any]] = run_lane,
) -> Path:
    plan, profile, workflow = build_plan(
        settings,
        dataset_ref=dataset_ref,
        profile_path=profile_path,
        output_dir=output_dir,
        golden_checks=golden_checks,
    )
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    _write_json(output / "plan.json", plan)
    runtime_workflow = render_runtime_workflow(
        workflow,
        Path(plan["dataset"]["provider_uri"]),
        output / "runtime_workflow.yaml",
    )

    resolved = resolve_dataset(settings, dataset_ref, allow_legacy=False)
    verification: dict[str, object] = {}
    verify_dataset_manifest(resolved.manifest_path, mode="sampled", sample_size=64, evidence=verification)

    seeds = [int(value) for value in profile["seeds"]]
    native = lane_runner(
        runtime_workflow,
        lane="native",
        seed=seeds[0],
        lane_root=output / "native" / f"seed_{seeds[0]}",
    )
    platform = [
        lane_runner(
            runtime_workflow,
            lane="platform",
            seed=seed,
            lane_root=output / "platform" / f"seed_{seed}",
        )
        for seed in seeds
    ]
    engine = compare_engine_parity(native, platform[0], profile)
    vendor = evaluate_vendor_parity(
        platform,
        profile,
        data_audit=plan["data_semantics"],
        golden=plan["golden_checks"],
    )
    status = "PASS" if engine["passed"] and vendor["passed"] else "FAIL"
    report = {
        "schema_version": REPORT_SCHEMA,
        "profile_id": profile["profile_id"],
        "scientific_identity": plan["scientific_identity"],
        "status": status,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": plan["dataset"],
        "upstream": dict(profile.get("upstream") or {}),
        "environment": {
            "python": sys.version,
            "packages": _package_versions(),
            "git": git_revision(Path(__file__).resolve().parents[4]),
        },
        "workflow": {
            "frozen_sha256": plan["profile"]["workflow_sha256"],
            "runtime_sha256": sha256_file(runtime_workflow),
            "runtime_path": str(runtime_workflow),
        },
        "verification": verification,
        "data_semantics": plan["data_semantics"],
        "golden_checks": plan["golden_checks"],
        "engine_parity": engine,
        "vendor_parity": vendor,
        "seeds": seeds,
        "artifacts": {
            "native": _serializable_lane(native),
            "platform": [_serializable_lane(item) for item in platform],
        },
        "automatic_model_selection": False,
        "automatic_promotion": False,
    }
    report_path = _write_json(output / "parity_report.json", report)
    _write_markdown(report, output / "parity_report.md")
    return report_path


def _parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="tq-official-parity")
    sub = root.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default="configs/pipeline.standalone.yaml")
    common.add_argument(
        "--profile",
        default=resource_argument("configs/research/qlib_official_alpha158_lgb_v1.yaml"),
    )
    common.add_argument("--dataset-ref", required=True)
    common.add_argument("--output-dir", required=True)
    common.add_argument("--golden-checks")
    sub.add_parser("plan", parents=[common])
    sub.add_parser("run", parents=[common])
    return root


def main() -> int:
    args = _parser().parse_args()
    settings = Settings.load(args.config, require_tushare=False, create_dirs=False)
    if args.command == "plan":
        plan, _, _ = build_plan(
            settings,
            dataset_ref=args.dataset_ref,
            profile_path=args.profile,
            output_dir=args.output_dir,
            golden_checks=args.golden_checks,
        )
        path = _write_json(Path(args.output_dir).expanduser().resolve() / "plan.json", plan)
        print(json.dumps({"plan": str(path), "scientificIdentity": plan["scientific_identity"]}))
        return 0
    report_path = run_official_parity(
        settings,
        dataset_ref=args.dataset_ref,
        profile_path=args.profile,
        output_dir=args.output_dir,
        golden_checks=args.golden_checks,
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    print(json.dumps({"report": str(report_path), "status": report["status"]}, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
