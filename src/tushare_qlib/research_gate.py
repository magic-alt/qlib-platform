from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ResearchThresholds:
    min_observations: int = 252
    min_ic_mean: float = 0.01
    min_rank_ic_mean: float = 0.02
    min_icir: float = 0.50
    min_long_short_annualized: float = 0.05
    min_excess_ir: float = 0.50
    max_drawdown: float = 0.20
    require_unique_artifact: bool = True

    @classmethod
    def from_mapping(cls, data: Mapping[str, object] | None) -> "ResearchThresholds":
        data = data or {}
        return cls(
            min_observations=int(str(data.get("min_observations", cls.min_observations))),
            min_ic_mean=float(str(data.get("min_ic_mean", cls.min_ic_mean))),
            min_rank_ic_mean=float(str(data.get("min_rank_ic_mean", cls.min_rank_ic_mean))),
            min_icir=float(str(data.get("min_icir", cls.min_icir))),
            min_long_short_annualized=float(
                str(data.get("min_long_short_annualized", cls.min_long_short_annualized))
            ),
            min_excess_ir=float(str(data.get("min_excess_ir", cls.min_excess_ir))),
            max_drawdown=float(str(data.get("max_drawdown", cls.max_drawdown))),
            require_unique_artifact=_bool_value(
                data.get("require_unique_artifact", cls.require_unique_artifact)
            ),
        )


@dataclass(frozen=True)
class GateCheck:
    name: str
    value: object
    threshold: object
    passed: bool


class ResearchPromotionError(RuntimeError):
    def __init__(self, manifest_path: str | Path):
        self.manifest_path = Path(manifest_path)
        super().__init__(f"research run was REJECTED; audit manifest: {self.manifest_path}")


def _bool_value(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _as_series(value: pd.Series | pd.DataFrame, name: str) -> pd.Series:
    if isinstance(value, pd.DataFrame):
        if value.shape[1] != 1:
            raise ValueError(f"{name} must contain exactly one column")
        return value.iloc[:, 0]
    return value


def derive_research_metrics(
    predictions: pd.Series | pd.DataFrame,
    labels: pd.Series | pd.DataFrame,
    portfolio_report: pd.DataFrame,
    *,
    unique_artifact: bool,
    lineage_complete: bool,
) -> dict[str, object]:
    """Derive the complete promotion metric contract from OOS artifacts."""

    score = pd.to_numeric(_as_series(predictions, "predictions"), errors="coerce").rename("score")
    label = pd.to_numeric(_as_series(labels, "labels"), errors="coerce").rename("label")
    paired = pd.concat([score, label], axis=1, join="inner").dropna()
    if not isinstance(paired.index, pd.MultiIndex) or "datetime" not in paired.index.names:
        raise ValueError("predictions and labels must use a MultiIndex containing datetime")

    by_date = paired.groupby(level="datetime", sort=True)
    ic = by_date.apply(lambda frame: frame["score"].corr(frame["label"])).dropna()
    rank_ic = by_date.apply(lambda frame: frame["score"].corr(frame["label"], method="spearman")).dropna()

    def long_short(frame: pd.DataFrame) -> float:
        count = max(1, len(frame) // 5)
        ranked = frame.sort_values("score", ascending=False)
        return float(ranked.head(count)["label"].mean() - ranked.tail(count)["label"].mean())

    long_short_daily = by_date.apply(long_short).dropna()

    def report_series(column: str) -> pd.Series:
        if column not in portfolio_report:
            return pd.Series(0.0, index=portfolio_report.index, dtype=float)
        return pd.to_numeric(portfolio_report[column], errors="coerce")

    returns = report_series("return")
    benchmark = report_series("bench")
    costs = report_series("cost")
    excess = (returns - benchmark - costs).dropna()
    net = (returns - costs).dropna()
    equity = pd.concat([pd.Series([1.0], index=["__initial__"]), (1.0 + net).cumprod()])
    drawdown = equity / equity.cummax() - 1.0

    def ratio(values: pd.Series, *, annualize: bool = False) -> float:
        std = float(values.std(ddof=1))
        if not np.isfinite(std) or std <= 0:
            return float("-inf")
        result = float(values.mean()) / std
        return result * np.sqrt(252.0) if annualize else result

    return {
        "observations": int(min(len(ic), len(rank_ic))),
        "ic_mean": float(ic.mean()) if len(ic) else float("-inf"),
        "rank_ic_mean": float(rank_ic.mean()) if len(rank_ic) else float("-inf"),
        "icir": ratio(ic),
        "long_short_annualized": float(long_short_daily.mean() * 252.0)
        if len(long_short_daily)
        else float("-inf"),
        "excess_ir": ratio(excess, annualize=True),
        "max_drawdown": float(drawdown.min()) if len(drawdown) else float("-inf"),
        "unique_artifact": bool(unique_artifact),
        "lineage_complete": bool(lineage_complete),
    }


def evaluate_research_metrics(
    metrics: Mapping[str, object],
    thresholds: ResearchThresholds | None = None,
    *,
    allow_dirty_research: bool = False,
) -> dict[str, object]:
    thresholds = thresholds or ResearchThresholds()
    observations = int(str(metrics.get("observations", 0)))
    ic_mean = float(str(metrics.get("ic_mean", float("-inf"))))
    rank_ic_mean = float(str(metrics.get("rank_ic_mean", float("-inf"))))
    icir = float(str(metrics.get("icir", float("-inf"))))
    long_short = float(str(metrics.get("long_short_annualized", float("-inf"))))
    excess_ir = float(str(metrics.get("excess_ir", float("-inf"))))
    max_drawdown = abs(float(str(metrics.get("max_drawdown", float("inf")))))
    checks = [
        GateCheck(
            "observations",
            observations,
            thresholds.min_observations,
            observations >= thresholds.min_observations,
        ),
        GateCheck(
            "ic_mean",
            ic_mean,
            thresholds.min_ic_mean,
            ic_mean >= thresholds.min_ic_mean,
        ),
        GateCheck(
            "rank_ic_mean",
            rank_ic_mean,
            thresholds.min_rank_ic_mean,
            rank_ic_mean >= thresholds.min_rank_ic_mean,
        ),
        GateCheck(
            "icir",
            icir,
            thresholds.min_icir,
            icir >= thresholds.min_icir,
        ),
        GateCheck(
            "long_short_annualized",
            long_short,
            thresholds.min_long_short_annualized,
            long_short >= thresholds.min_long_short_annualized,
        ),
        GateCheck(
            "excess_ir",
            excess_ir,
            thresholds.min_excess_ir,
            excess_ir >= thresholds.min_excess_ir,
        ),
        GateCheck(
            "max_drawdown",
            max_drawdown,
            thresholds.max_drawdown,
            max_drawdown <= thresholds.max_drawdown,
        ),
        GateCheck(
            "unique_artifact",
            bool(metrics.get("unique_artifact", False)),
            thresholds.require_unique_artifact,
            bool(metrics.get("unique_artifact", False)) or not thresholds.require_unique_artifact,
        ),
        GateCheck(
            "lineage_complete",
            bool(metrics.get("lineage_complete", False)),
            "complete or dirty-research override" if allow_dirty_research else True,
            bool(metrics.get("lineage_complete", False)) or allow_dirty_research,
        ),
    ]
    passed = all(check.passed for check in checks)
    return {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "decision": "PROMOTE" if passed else "REJECT",
        "passed": passed,
        "metrics": dict(metrics),
        "thresholds": asdict(thresholds),
        "checks": [asdict(c) for c in checks],
    }


def evaluate_component_metrics(
    metrics: Mapping[str, object], *, allow_dirty_research: bool = False
) -> dict[str, object]:
    """Validate a rolling component without granting release promotion.

    Short folds establish chronological out-of-sample evidence, but statistical
    promotion belongs to their combined OOS series.  Component validation only
    checks that evidence exists, is unique and has complete lineage.
    """

    checks = [
        GateCheck(
            "observations",
            int(str(metrics.get("observations", 0))),
            "> 0",
            int(str(metrics.get("observations", 0))) > 0,
        ),
        GateCheck(
            "unique_artifact",
            bool(metrics.get("unique_artifact", False)),
            True,
            bool(metrics.get("unique_artifact", False)),
        ),
        GateCheck(
            "lineage_complete",
            bool(metrics.get("lineage_complete", False)),
            "complete or dirty-research override" if allow_dirty_research else True,
            bool(metrics.get("lineage_complete", False)) or allow_dirty_research,
        ),
    ]
    passed = all(check.passed for check in checks)
    return {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "decision": "COMPONENT_VALIDATED" if passed else "REJECT",
        "passed": passed,
        "metrics": dict(metrics),
        "thresholds": {"mode": "component_validation"},
        "checks": [asdict(check) for check in checks],
    }


def write_gate_report(report: Mapping[str, object], path: str | Path) -> Path:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    return target
