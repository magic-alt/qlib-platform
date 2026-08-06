from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping


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
        values = {name: data[name] for name in cls.__dataclass_fields__ if name in data}
        return cls(**values)


@dataclass(frozen=True)
class GateCheck:
    name: str
    value: object
    threshold: object
    passed: bool


def evaluate_research_metrics(
    metrics: Mapping[str, object], thresholds: ResearchThresholds | None = None
) -> dict[str, object]:
    thresholds = thresholds or ResearchThresholds()
    checks = [
        GateCheck("observations", int(metrics.get("observations", 0)), thresholds.min_observations, int(metrics.get("observations", 0)) >= thresholds.min_observations),
        GateCheck("ic_mean", float(metrics.get("ic_mean", float("-inf"))), thresholds.min_ic_mean, float(metrics.get("ic_mean", float("-inf"))) >= thresholds.min_ic_mean),
        GateCheck("rank_ic_mean", float(metrics.get("rank_ic_mean", float("-inf"))), thresholds.min_rank_ic_mean, float(metrics.get("rank_ic_mean", float("-inf"))) >= thresholds.min_rank_ic_mean),
        GateCheck("icir", float(metrics.get("icir", float("-inf"))), thresholds.min_icir, float(metrics.get("icir", float("-inf"))) >= thresholds.min_icir),
        GateCheck(
            "long_short_annualized",
            float(metrics.get("long_short_annualized", float("-inf"))),
            thresholds.min_long_short_annualized,
            float(metrics.get("long_short_annualized", float("-inf"))) >= thresholds.min_long_short_annualized,
        ),
        GateCheck("excess_ir", float(metrics.get("excess_ir", float("-inf"))), thresholds.min_excess_ir, float(metrics.get("excess_ir", float("-inf"))) >= thresholds.min_excess_ir),
        GateCheck(
            "max_drawdown",
            abs(float(metrics.get("max_drawdown", float("inf")))),
            thresholds.max_drawdown,
            abs(float(metrics.get("max_drawdown", float("inf")))) <= thresholds.max_drawdown,
        ),
        GateCheck(
            "unique_artifact",
            bool(metrics.get("unique_artifact", False)),
            thresholds.require_unique_artifact,
            bool(metrics.get("unique_artifact", False)) or not thresholds.require_unique_artifact,
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


def write_gate_report(report: Mapping[str, object], path: str | Path) -> Path:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    return target
