from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from .canonical_config import StrategySpec
from .settings import Settings


_PARTICIPATION_EXPRESSION = re.compile(
    r"^\s*\$volume\s*\*\s*([+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*$"
)
_LIMIT_EXPRESSIONS = ("$is_limit_up > 0", "$is_limit_down > 0")


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> tuple[object, ...] | None:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(value)
    return None


def _participation(value: object) -> float | None:
    parts = _sequence(value)
    if parts is None or len(parts) != 2 or parts[0] != "current" or not isinstance(parts[1], str):
        return None
    match = _PARTICIPATION_EXPRESSION.fullmatch(parts[1])
    return float(match.group(1)) if match else None


def _add_mismatch(mismatches: dict[str, object], key: str, expected: object, actual: object) -> None:
    if actual != expected:
        mismatches[key] = {"pipeline": expected, "qrun": actual}


def _result(mismatches: dict[str, object], uncovered: dict[str, object] | None = None) -> dict[str, object]:
    uncovered = uncovered or {}
    certified = not mismatches and not uncovered
    return {
        "passed": not mismatches,
        "mismatches": mismatches,
        "certifiedExecutionEquivalent": certified,
        "certificationStatus": "certified" if certified else "non-certified",
        "uncoveredSemantics": uncovered,
    }


def validate_qrun_contract(settings: Settings, workflow_path: str | Path) -> dict[str, object]:
    """Compare qrun's static strategy, execution and universe semantics.

    This deliberately performs configuration-only validation: it neither imports
    Qlib/model packages nor opens a dataset.  A scalar Qlib price-limit threshold
    is not equivalent to the integrated runner's directional limit fields and is
    therefore a mismatch, not an informational caveat.
    """
    workflow = yaml.safe_load(Path(workflow_path).read_text(encoding="utf-8")) or {}
    task = _mapping(workflow.get("task"))
    records = task.get("record", [])
    if not isinstance(records, list):
        records = []
    port_record = next(
        (item for item in records if isinstance(item, Mapping) and item.get("class") == "PortAnaRecord"), None
    )
    if port_record is None:
        return _result({"PortAnaRecord": {"pipeline": "present", "qrun": "missing"}})

    config = _mapping(_mapping(port_record.get("kwargs")).get("config"))
    qrun_strategy = _mapping(_mapping(config.get("strategy")).get("kwargs"))
    qrun_backtest = _mapping(config.get("backtest"))
    qrun_exchange = _mapping(qrun_backtest.get("exchange_kwargs"))
    mismatches: dict[str, object] = {}

    expected_strategy = StrategySpec.from_settings(settings).to_policy().__dict__
    for key, expected in expected_strategy.items():
        _add_mismatch(mismatches, f"strategy.{key}", expected, qrun_strategy.get(key))

    research = _mapping(settings.data.get("research"))
    execution_expected = {
        "deal_price": research.get("deal_price", "open"),
        "trade_unit": int(str(research.get("trade_unit", 100))),
        "open_cost": float(str(research.get("open_cost", 0.00035))),
        "close_cost": float(str(research.get("close_cost", 0.00085))),
        "min_cost": float(str(research.get("min_cost", 5))),
    }
    for key, expected in execution_expected.items():
        _add_mismatch(mismatches, f"execution.{key}", expected, qrun_exchange.get(key))

    volume_threshold = qrun_exchange.get("volume_threshold")
    volume_parts = _sequence(volume_threshold)
    expected_participation = float(str(research.get("max_participation_rate", 0.05)))
    _add_mismatch(
        mismatches,
        "execution.volume_threshold.mode",
        "current",
        volume_parts[0] if volume_parts else None,
    )
    _add_mismatch(
        mismatches,
        "execution.max_participation_rate",
        expected_participation,
        _participation(volume_threshold),
    )

    limit_threshold = qrun_exchange.get("limit_threshold")
    limit_parts = _sequence(limit_threshold)
    normalized_limit = tuple(str(part).strip() for part in limit_parts) if limit_parts else limit_threshold
    _add_mismatch(mismatches, "execution.limit_threshold", _LIMIT_EXPRESSIONS, normalized_limit)

    _add_mismatch(
        mismatches,
        "benchmark",
        str(research.get("benchmark", "SH000300")),
        qrun_backtest.get("benchmark"),
    )

    dataset = _mapping(task.get("dataset"))
    handler = _mapping(_mapping(dataset.get("kwargs")).get("handler"))
    handler_kwargs = _mapping(handler.get("kwargs"))
    universe = _mapping(settings.data.get("universe"))
    _add_mismatch(
        mismatches,
        "universe.instruments",
        universe.get("instruments", "all"),
        handler_kwargs.get("instruments"),
    )
    processors = handler_kwargs.get("shared_processors", [])
    if not isinstance(processors, list):
        processors = []
    universe_processor = next(
        (
            _mapping(item.get("kwargs"))
            for item in processors
            if isinstance(item, Mapping) and item.get("class") == "AshareUniverseFilter"
        ),
        {},
    )
    filter_expected: dict[str, object] = {
        "min_listed_days": int(str(universe.get("min_listed_days", 120))),
        "min_circ_mv_yuan": float(str(universe.get("min_circ_mv_yuan", 2_000_000_000))),
        "min_money_20d_yuan": float(str(universe.get("min_money_20d_yuan", 20_000_000))),
        "exclude_st": bool(universe.get("exclude_st", True)),
        "allow_unknown_st": bool(universe.get("allow_unknown_st", False)),
    }
    for key, expected in filter_expected.items():
        _add_mismatch(mismatches, f"universe.{key}", expected, universe_processor.get(key))

    uncovered: dict[str, object] = {}
    if "execution.limit_threshold" in mismatches:
        uncovered["limit_threshold"] = {
            "integratedRunner": list(_LIMIT_EXPRESSIONS),
            "qrun": normalized_limit,
            "reason": "qrun limit behavior is not semantically equivalent to directional limit fields",
        }
    return _result(mismatches, uncovered)
