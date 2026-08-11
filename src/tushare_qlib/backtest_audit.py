from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Any

import pandas as pd


def audit_mlflow_run(run_dir: str | Path) -> dict[str, Any]:
    root = Path(run_dir).expanduser().resolve()
    artifacts = root / "artifacts" if (root / "artifacts").is_dir() else root
    errors: list[str] = []
    pred_path = artifacts / "pred.pkl"
    report_path = artifacts / "portfolio_analysis" / "report_normal_1day.pkl"
    config_path = artifacts / "config"
    if not pred_path.exists():
        errors.append("missing_pred")
    if not report_path.exists():
        errors.append("missing_portfolio_report")
    pred = pd.read_pickle(pred_path) if pred_path.exists() else pd.DataFrame()
    report = pd.read_pickle(report_path) if report_path.exists() else pd.DataFrame()
    config: dict[str, Any] = {}
    if config_path.exists():
        with config_path.open("rb") as fp:
            loaded = pickle.load(fp)
        config = loaded if isinstance(loaded, dict) else {}
    pred_dates = (
        pred.index.get_level_values("datetime")
        if not pred.empty and "datetime" in pred.index.names
        else pd.Index([])
    )
    if len(pred_dates) and not report.empty:
        if pd.Timestamp(report.index.min()) < pd.Timestamp(pred_dates.min()):
            errors.append("portfolio_starts_before_predictions")
        if pd.Timestamp(report.index.max()) > pd.Timestamp(pred_dates.max()):
            errors.append("portfolio_ends_after_predictions")
    benchmark = config.get("benchmark")
    manifest_path = Path.cwd() / "data" / "output" / "research" / root.name / "manifest.json"
    if benchmark is None and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        execution = manifest.get("execution") if isinstance(manifest, dict) else {}
        benchmark = execution.get("benchmark") if isinstance(execution, dict) else None
    if benchmark is None:
        errors.append("benchmark_not_recorded")
    if benchmark and str(benchmark).upper() != "SH000300":
        errors.append(f"uncertified_benchmark:{benchmark}")
    if "account" in report and (pd.to_numeric(report["account"], errors="coerce") < 0).any():
        errors.append("negative_account_value")
    summary = {
        "prediction_start": str(pd.Timestamp(pred_dates.min()).date()) if len(pred_dates) else None,
        "prediction_end": str(pd.Timestamp(pred_dates.max()).date()) if len(pred_dates) else None,
        "report_start": str(pd.Timestamp(report.index.min()).date()) if not report.empty else None,
        "report_end": str(pd.Timestamp(report.index.max()).date()) if not report.empty else None,
        "benchmark": benchmark,
    }
    for column in ("return", "bench", "cost"):
        if column in report:
            values = pd.to_numeric(report[column], errors="coerce").dropna()
            summary[f"{column}_total"] = float((1.0 + values).prod() - 1.0)
    return {
        "schema_version": "1.0",
        "passed": not errors,
        "errors": errors,
        "summary": summary,
        "run_dir": str(root),
    }


def write_audit(report: dict[str, Any], output: str | Path | None = None) -> Path:
    path = (
        Path(output).expanduser().resolve()
        if output
        else Path(report["run_dir"]) / "qlib_backtest_audit.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
