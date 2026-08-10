from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml

from .canonical_config import CanonicalConfig
from .model_runtime import load_model_profile, resolve_runtime
from .settings import Settings


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def validate_qrun_contract(settings: Settings, workflow_path: str | Path) -> dict[str, object]:
    """Reject qrun templates whose execution semantics drift from pipeline YAML."""
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
    # Runtime is irrelevant to this comparison; only pipeline settings are read.
    canonical = CanonicalConfig.from_settings(settings, resolve_runtime(load_model_profile(settings)))
    expected = canonical.strategy.to_policy().__dict__
    actual = {key: qrun_strategy.get(key) for key in expected}
    research = _mapping(settings.data.get("research"))
    execution_expected = {
        "deal_price": research.get("deal_price"), "trade_unit": research.get("trade_unit"),
        "open_cost": research.get("open_cost"), "close_cost": research.get("close_cost"),
        "min_cost": research.get("min_cost"),
    }
    execution_actual = {key: qrun_exchange.get(key) for key in execution_expected}
    mismatches = {key: {"pipeline": expected[key], "qrun": actual[key]} for key in expected if actual[key] != expected[key]}
    mismatches.update({key: {"pipeline": value, "qrun": execution_actual[key]} for key, value in execution_expected.items() if execution_actual[key] != value})
    return {"passed": not mismatches, "mismatches": mismatches}
