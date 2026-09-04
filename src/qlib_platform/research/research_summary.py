from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Summarize qlib-platform local research experiment matrices")
    p.add_argument("matrix", help="research_matrix.json written by the local research quickstart")
    p.add_argument("--output", help="output directory; defaults beside the matrix")
    return p


def _manifest(result: object) -> tuple[Path, dict[str, Any]] | None:
    if not isinstance(result, Mapping) or not result.get("manifest"):
        return None
    path = Path(str(result["manifest"])).expanduser().resolve()
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return (path, payload) if isinstance(payload, dict) else None


def _artifact(manifest: Mapping[str, Any], name: str) -> Path | None:
    for item in manifest.get("artifacts", []):
        if isinstance(item, Mapping) and item.get("name") == name and item.get("localPath"):
            path = Path(str(item["localPath"])).expanduser().resolve()
            if path.is_file():
                return path
    return None


def _portfolio_metrics(manifest: Mapping[str, Any]) -> dict[str, Any]:
    metrics = dict(manifest.get("metrics", {})) if isinstance(manifest.get("metrics"), Mapping) else {}
    report_path = _artifact(manifest, "portfolio_report.parquet")
    if report_path is None:
        return metrics
    report = pd.read_parquet(report_path)

    def series(column: str) -> pd.Series:
        if column not in report:
            return pd.Series(0.0, index=report.index, dtype=float)
        return pd.to_numeric(report[column], errors="coerce").fillna(0.0)

    returns = series("return")
    benchmark = series("bench")
    costs = series("cost")
    excess = returns - benchmark - costs
    net = returns - costs
    std = float(excess.std(ddof=1))
    metrics.setdefault(
        "excess_ir",
        float(excess.mean()) / std * np.sqrt(252.0) if np.isfinite(std) and std > 0 else None,
    )
    equity = pd.concat([pd.Series([1.0]), (1.0 + net).cumprod().reset_index(drop=True)])
    metrics.setdefault("max_drawdown", float((equity / equity.cummax() - 1.0).min()))
    metrics.setdefault("cost_total", float((1.0 + costs).prod() - 1.0))
    if "turnover" in report:
        turnover = pd.to_numeric(report["turnover"], errors="coerce").dropna()
        metrics.setdefault("turnover_mean", float(turnover.mean()) if len(turnover) else None)
        metrics.setdefault("turnover_total", float(turnover.sum()) if len(turnover) else None)
    return metrics


def summarize_job(job: Mapping[str, Any]) -> dict[str, Any]:
    research_entry = _manifest(job.get("result"))
    research_manifest = research_entry[1] if research_entry else {}
    signal = (
        dict(research_manifest.get("metrics", {}))
        if isinstance(research_manifest.get("metrics"), Mapping)
        else {}
    )
    backtest = job.get("predictionBacktest")
    backtest_result = backtest.get("result") if isinstance(backtest, Mapping) else None
    portfolio_entry = _manifest(backtest_result) or research_entry
    portfolio = _portfolio_metrics(portfolio_entry[1]) if portfolio_entry else {}
    return {
        "alphaPack": job.get("alphaPack"),
        "model": job.get("model"),
        "status": job.get("status", "UNKNOWN"),
        "researchManifest": str(research_entry[0]) if research_entry else None,
        "portfolioManifest": str(portfolio_entry[0]) if portfolio_entry else None,
        "icMean": signal.get("ic_mean"),
        "rankIcMean": signal.get("rank_ic_mean"),
        "icir": signal.get("icir"),
        "rankIcir": signal.get("rank_icir"),
        "longShortAnnualized": signal.get("long_short_annualized"),
        "excessIr": portfolio.get("excess_ir"),
        "maxDrawdown": portfolio.get("max_drawdown"),
        "turnoverMean": portfolio.get("turnover_mean"),
        "turnoverTotal": portfolio.get("turnover_total"),
        "costTotal": portfolio.get("cost_total", portfolio.get("costTotal")),
        "returnTotal": portfolio.get("returnTotal"),
        "benchmarkTotal": portfolio.get("benchTotal"),
    }


def summarize_matrix(matrix: str | Path) -> dict[str, Any]:
    path = Path(matrix).expanduser().resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise ValueError("research matrix must contain a jobs list")
    return {
        "schemaVersion": "1.0",
        "sourceMatrix": str(path),
        "datasetRef": payload.get("datasetRef"),
        "mode": payload.get("mode"),
        "stage": payload.get("stage"),
        "jobs": [summarize_job(job) for job in payload["jobs"] if isinstance(job, Mapping)],
        "metricSemantics": {
            "icIcir": "from research manifest signal metrics",
            "excessIrMaxDrawdown": "from governed metrics when present, otherwise recomputed from portfolio_report",
            "turnover": "mean/sum of portfolio_report.turnover when Qlib exposes the column; never inferred",
            "cost": "costTotal/cost_total from portfolio evidence",
        },
    }


def _number(value: object) -> str:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "-"
    return f"{number:.3f}" if np.isfinite(number) else "-"


def _percent(value: object) -> str:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "-"
    return f"{number:.2%}" if np.isfinite(number) else "-"


def render_markdown(summary: Mapping[str, Any]) -> str:
    lines = [
        "# Local Research Comparison",
        "",
        f"Dataset: `{summary.get('datasetRef')}`  ",
        f"Mode: `{summary.get('mode')}`  ",
        f"Stage: `{summary.get('stage')}`",
        "",
        "| AlphaPack | Model | IC | RankIC | ICIR | RankICIR | ExcessIR | MDD | Turnover | Cost | Status |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for job in summary.get("jobs", []):
        if not isinstance(job, Mapping):
            continue
        lines.append(
            f"| {job.get('alphaPack')} | {job.get('model')} | {_number(job.get('icMean'))} | "
            f"{_number(job.get('rankIcMean'))} | {_number(job.get('icir'))} | "
            f"{_number(job.get('rankIcir'))} | {_number(job.get('excessIr'))} | "
            f"{_percent(job.get('maxDrawdown'))} | {_percent(job.get('turnoverMean'))} | "
            f"{_percent(job.get('costTotal'))} | {job.get('status')} |"
        )
    lines.extend(
        [
            "",
            "`Turnover` is reported only when Qlib's portfolio report exposes a turnover field. Missing values are "
            "left blank rather than estimated.",
            "",
            "> This is research comparison evidence, not candidate selection or publishing authorization.",
            "",
        ]
    )
    return "\n".join(lines)


def write_summary(matrix: str | Path, output: str | Path | None = None) -> tuple[Path, Path]:
    summary = summarize_matrix(matrix)
    matrix_path = Path(matrix).expanduser().resolve()
    root = Path(output).expanduser().resolve() if output else matrix_path.parent
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "research_comparison.json"
    md_path = root / "research_comparison.md"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(summary), encoding="utf-8")
    return json_path, md_path


def main() -> int:
    args = parser().parse_args()
    json_path, md_path = write_summary(args.matrix, args.output)
    print(json.dumps({"summaryJson": str(json_path), "summaryMarkdown": str(md_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
