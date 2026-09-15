from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from qlib_platform.research.contracts.ashare_etf import ETF_FORBIDDEN_STOCK_DATASETS
from qlib_platform.research.workflow.etf_fixture import EtfCertificationResult


def render_etf_certification_report(result: EtfCertificationResult) -> str:
    """Render a deterministic research-only report for the ETF certification fixture."""

    backtest = result.daily_backtest
    gross = _compound(backtest["gross_return"])
    net = _compound(backtest["net_return"])
    max_drawdown = _max_drawdown(backtest["net_return"])
    diagnostics = result.diagnostics.summary
    features = (
        [str(value) for value in diagnostics["feature"].tolist()]
        if "feature" in diagnostics.columns
        else []
    )
    lines = [
        "# A-share ETF Research Fixture Report",
        "",
        "## Contract",
        "",
        "- ResearchProfile: `ashare_etf_v1`",
        "- AlphaPack: `etf_core_v1`",
        "- Promotion scope: `research_only`",
        "- Execution authority: `none`",
        "- Final holdout access: `false`",
        f"- Forbidden stock-only datasets: `{', '.join(ETF_FORBIDDEN_STOCK_DATASETS)}`",
        "",
        "## Training and OOS",
        "",
        f"- Training rows: {result.train_rows}",
        f"- OOS evaluation rows: {result.evaluation_rows}",
        f"- Model coefficients: {len(result.coefficients)}",
        "",
        "## Portfolio backtest",
        "",
        "The daily portfolio uses one-session realized returns. The governed 5-day research label is not reused as daily PnL.",
        "",
        f"- Sessions: {len(backtest)}",
        f"- Gross cumulative return: {gross:.6f}",
        f"- Net cumulative return: {net:.6f}",
        f"- Max drawdown: {max_drawdown:.6f}",
        f"- Total transaction cost: {float(backtest['transaction_cost'].sum()):.6f}",
        "",
        "## Feature diagnostics",
        "",
        f"- Diagnosed features: {len(features)}",
        f"- Feature names: `{', '.join(features)}`",
        "",
        "## Boundary",
        "",
        "This artifact is research evidence only. It does not authorize publishing, Paper/Live execution, broker routing, or final-holdout access.",
        "",
    ]
    return "\n".join(lines)


def write_etf_certification_report(result: EtfCertificationResult, output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_etf_certification_report(result), encoding="utf-8")
    return path


def _compound(returns: pd.Series) -> float:
    values = pd.to_numeric(returns, errors="coerce").to_numpy(dtype=float)
    if values.size == 0 or not np.isfinite(values).all():
        raise ValueError("ETF report requires finite non-empty backtest returns")
    if (values <= -1.0).any():
        raise ValueError("ETF report cannot compound returns less than or equal to -100%")
    return float(np.prod(1.0 + values) - 1.0)


def _max_drawdown(returns: pd.Series) -> float:
    values = pd.to_numeric(returns, errors="coerce").to_numpy(dtype=float)
    wealth = np.cumprod(1.0 + values)
    peaks = np.maximum.accumulate(wealth)
    return float(np.max(1.0 - wealth / peaks))
