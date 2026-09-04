from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

_OPENCL_WARNING = re.compile(r"^\d+ warnings? generated\.$")
_KNOWN_NOISE_PREFIXES = (
    "ModuleNotFoundError. CatBoostModel are skipped.",
    "ModuleNotFoundError.  PyTorch models are skipped",
    "Gym has been unmaintained since 2022",
    "Please upgrade to Gymnasium",
    "Users of this version of Gym should be able to simply replace",
    "See the migration guide at https://gymnasium.farama.org/",
    "Downloading artifacts:",
    "A value is trying to be set on a copy of a slice from a DataFrame.",
    "Try using .loc[row_indexer,col_indexer] = value instead",
    "See the caveats in the documentation: https://pandas.pydata.org/",
)


def filter_known_child_noise(text: str) -> str:
    """Remove only deterministic, known-nonfatal upstream chatter.

    Tracebacks, arbitrary warnings and model-training output are intentionally preserved.
    ``--verbose-child-output`` in the quickstart bypasses this filter entirely.
    """

    kept: list[str] = []
    for raw in text.replace("\r", "\n").splitlines():
        stripped = raw.strip()
        normalized = stripped.replace("\\", "/")
        if not stripped:
            continue
        if _OPENCL_WARNING.fullmatch(stripped):
            continue
        if stripped.startswith(_KNOWN_NOISE_PREFIXES):
            continue
        if (
            "site-packages/qlib/data/dataset/processor.py:358: SettingWithCopyWarning:" in normalized
            or stripped == "df[cols] = t"
        ):
            continue
        kept.append(raw)
    return "\n".join(kept) + ("\n" if kept else "")


def result_manifest_path(output_root: Path, result: Mapping[str, Any] | None) -> Path | None:
    if not result:
        return None
    explicit = result.get("manifest")
    if explicit:
        path = Path(str(explicit)).expanduser()
        if path.is_file():
            return path
    run_id = str(result.get("runId") or "").strip()
    if not run_id:
        return None
    fallback = output_root / "research" / run_id / "manifest.json"
    return fallback if fallback.is_file() else None


def summarize_result(output_root: Path, result: Mapping[str, Any] | None) -> dict[str, Any] | None:
    path = result_manifest_path(output_root, result)
    if path is None:
        return None
    manifest = json.loads(path.read_text(encoding="utf-8"))
    dataset = manifest.get("dataset", {})
    dataset = dataset if isinstance(dataset, Mapping) else {}
    runtime = manifest.get("runtime", {})
    runtime = runtime if isinstance(runtime, Mapping) else {}
    metrics = manifest.get("metrics", {})
    metrics = metrics if isinstance(metrics, Mapping) else {}
    promotion = manifest.get("promotion", {})
    promotion = promotion if isinstance(promotion, Mapping) else {}
    feature_store = manifest.get("featureStore", {})
    feature_store = feature_store if isinstance(feature_store, Mapping) else {}
    timings = manifest.get("timings", {})
    timings = timings if isinstance(timings, Mapping) else {}
    phases = timings.get("phasesSeconds", {})
    phases = phases if isinstance(phases, Mapping) else {}

    metric_keys = (
        "ic_mean",
        "icir",
        "rank_ic_mean",
        "rank_icir",
        "long_short_annualized",
        "excess_ir",
        "max_drawdown",
        "returnTotal",
        "benchTotal",
        "costTotal",
    )
    timing_keys = (
        "feature_store_seconds",
        "handler_process_seconds",
        "train_seconds",
        "predict_seconds",
        "portfolio_engine_seconds",
    )
    summary: dict[str, Any] = {
        "runId": str(manifest.get("externalRunId", path.parent.name)),
        "manifest": str(path),
        "datasetVersionId": dataset.get("versionId") or dataset.get("datasetVersionId"),
        "datasetId": dataset.get("datasetId"),
        "modelProfile": runtime.get("modelProfile") or manifest.get("model", {}).get("name"),
        "resolvedDevice": runtime.get("resolvedDevice"),
        "deviceName": runtime.get("deviceName"),
        "decision": promotion.get("decision"),
        "promotionStatus": promotion.get("status"),
        "featureSnapshotId": feature_store.get("featureSnapshotId"),
        "featureCacheStatus": feature_store.get("cacheStatus"),
        "metrics": {key: metrics[key] for key in metric_keys if key in metrics},
        "timings": {key: phases[key] for key in timing_keys if key in phases},
        "totalSeconds": timings.get("totalSeconds"),
        "wallSeconds": timings.get("wallSeconds"),
        "peakRssMb": timings.get("peakRssMb"),
    }
    return {key: value for key, value in summary.items() if value not in (None, {}, "")}


def _fmt(value: object, digits: int = 4) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def render_terminal_summary(plan: Mapping[str, Any], root: Path) -> str:
    lines = ["", f"Research quickstart: {plan.get('status', 'UNKNOWN')}"]
    dataset = plan.get("dataset", {})
    if isinstance(dataset, Mapping) and dataset.get("versionId"):
        lines.append(f"DatasetVersion: {dataset['versionId']}")
    lines.append(f"Mode: {plan.get('mode', 'unknown')} | Stage: {plan.get('stage', 'unknown')}")

    jobs = plan.get("jobs", [])
    if isinstance(jobs, list):
        for job in jobs:
            if not isinstance(job, Mapping):
                continue
            lines.append("")
            lines.append(
                f"[{job.get('status', 'PLANNED')}] {job.get('alphaPack', '?')} x {job.get('model', '?')}"
            )
            summary = job.get("summary", {})
            if isinstance(summary, Mapping):
                device = summary.get("deviceName") or summary.get("resolvedDevice")
                resolved = summary.get("resolvedDevice")
                if device:
                    suffix = f" ({resolved})" if resolved and device != resolved else ""
                    lines.append(f"  Device: {device}{suffix}")
                metrics = summary.get("metrics", {})
                if isinstance(metrics, Mapping) and metrics:
                    selected = []
                    for key, label in (
                        ("ic_mean", "IC"),
                        ("icir", "ICIR"),
                        ("rank_ic_mean", "RankIC"),
                        ("rank_icir", "RankICIR"),
                    ):
                        if key in metrics:
                            selected.append(f"{label} {_fmt(metrics[key])}")
                    if selected:
                        lines.append("  Signal: " + " | ".join(selected))
                decision = summary.get("decision") or summary.get("promotionStatus")
                if decision:
                    lines.append(f"  Gate: {decision}")
                cache = summary.get("featureCacheStatus")
                snapshot = summary.get("featureSnapshotId")
                if cache or snapshot:
                    cache_text = str(cache or "UNKNOWN")
                    if snapshot:
                        cache_text += f" ({snapshot})"
                    lines.append(f"  Feature cache: {cache_text}")
                timings = summary.get("timings", {})
                timing_bits = []
                if isinstance(timings, Mapping):
                    for key, label in (
                        ("feature_store_seconds", "feature"),
                        ("train_seconds", "train"),
                        ("predict_seconds", "predict"),
                    ):
                        if key in timings:
                            timing_bits.append(f"{label} {_fmt(timings[key], 2)}s")
                if summary.get("totalSeconds") is not None:
                    timing_bits.append(f"total {_fmt(summary['totalSeconds'], 2)}s")
                if summary.get("peakRssMb") is not None:
                    timing_bits.append(f"RSS {_fmt(summary['peakRssMb'], 1)} MB")
                if timing_bits:
                    lines.append("  Timing: " + " | ".join(timing_bits))
                if summary.get("manifest"):
                    lines.append(f"  Manifest: {summary['manifest']}")
            backtest = job.get("predictionBacktest")
            if isinstance(backtest, Mapping):
                bt_status = "SUCCEEDED" if int(backtest.get("exitCode", 1)) == 0 else "FAILED"
                lines.append(f"  Prediction backtest: {bt_status}")

    lines.extend(
        [
            "",
            f"Matrix: {root / 'research_matrix.json'}",
            f"Summary: {root / 'research_matrix.md'}",
        ]
    )
    return "\n".join(lines)
