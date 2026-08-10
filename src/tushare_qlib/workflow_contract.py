from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml

from .canonical_config import StrategySpec
from .settings import Settings


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def validate_qrun_contract(settings: Settings, workflow_path: str | Path) -> dict[str, object]:
    """Compare the configuration-only subset shared by qrun and the release runner.

    qrun is an exploratory workflow and cannot express the release runner's
    per-side limit expressions.  The result therefore reports that semantic as
    uncovered instead of claiming that a successful comparison is certified
    execution equivalence.  Model imports, device probing and dataset access
    are intentionally outside this validation path.
    """
    workflow = yaml.safe_load(Path(workflow_path).read_text(encoding="utf-8")) or {}
    records = _mapping(workflow.get("task")).get("record", [])
    port_record = next(
        (item for item in records if isinstance(item, Mapping) and item.get("class") == "PortAnaRecord"), None
    )
    if port_record is None:
        return {"passed": False, "mismatches": {"PortAnaRecord": "missing"}}
    config = _mapping(_mapping(port_record.get("kwargs")).get("config"))
    qrun_strategy = _mapping(_mapping(config.get("strategy")).get("kwargs"))
    qrun_backtest = _mapping(config.get("backtest"))
    qrun_exchange = _mapping(qrun_backtest.get("exchange_kwargs"))
    expected = StrategySpec.from_settings(settings).to_policy().__dict__
    actual = {key: qrun_strategy.get(key) for key in expected}
    research = _mapping(settings.data.get("research"))
    participation = float(research.get("max_participation_rate", 0.05))
    execution_expected = {
        "deal_price": research.get("deal_price"),
        "trade_unit": research.get("trade_unit"),
        "open_cost": research.get("open_cost"),
        "close_cost": research.get("close_cost"),
        "min_cost": research.get("min_cost"),
        "volume_threshold": ["current", f"$volume * {participation}"],
    }
    execution_actual = {key: qrun_exchange.get(key) for key in execution_expected}
    mismatches = {
        key: {"pipeline": expected[key], "qrun": actual[key]}
        for key in expected
        if actual[key] != expected[key]
    }
    mismatches.update(
        {
            key: {"pipeline": value, "qrun": execution_actual[key]}
            for key, value in execution_expected.items()
            if execution_actual[key] != value
        }
    )
    benchmark = qrun_backtest.get("benchmark")
    expected_benchmark = research.get("benchmark")
    if benchmark != expected_benchmark:
        mismatches["benchmark"] = {"pipeline": expected_benchmark, "qrun": benchmark}
    return {
        "passed": not mismatches,
        "mismatches": mismatches,
        "certifiedExecutionEquivalent": False,
        "uncoveredSemantics": {
            "limit_threshold": {
                "integratedRunner": ["$is_limit_up > 0", "$is_limit_down > 0"],
                "qrun": qrun_exchange.get("limit_threshold"),
                "reason": "qrun safe YAML cannot encode the integrated runner's per-side expressions",
            }
        },
    }
