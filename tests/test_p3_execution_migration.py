from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from tushare_qlib.artifacts import ArtifactType
from tushare_qlib.cli import parser


ROOT = Path(__file__).resolve().parents[1]
RETIRED_COMMANDS = {
    "build-orders",
    "pretrade-risk",
    "record-broker-event",
    "reconcile-holdings",
    "build-topk-orders",
    "daily-action-run",
    "production-run",
    "production-replay",
    "shadow-run",
}
RETIRED_MODULES = {
    "execution.py",
    "risk_engine.py",
    "pretrade_runner.py",
    "holdings_ledger.py",
    "broker_state.py",
    "market_snapshot.py",
    "freshness.py",
    "production_orchestrator.py",
    "production_replay.py",
    "shadow_runner.py",
    "snapshot_audit.py",
}


def _commands() -> set[str]:
    command_parser = parser()
    action = next(item for item in command_parser._actions if isinstance(item, argparse._SubParsersAction))
    return set(action.choices)


def test_research_cli_has_no_execution_broker_or_ledger_commands():
    commands = _commands()
    assert commands.isdisjoint(RETIRED_COMMANDS)
    assert "build-target-portfolio" in commands


@pytest.mark.parametrize("command", sorted(RETIRED_COMMANDS))
def test_retired_execution_commands_fail_at_argument_parsing(command: str):
    with pytest.raises(SystemExit) as exc:
        parser().parse_args([command])
    assert exc.value.code == 2


def test_execution_broker_and_ledger_sources_are_physically_removed():
    package = ROOT / "src" / "tushare_qlib"
    assert not (package / "broker").exists()
    assert not (package / "qmt_gateway").exists()
    assert not {path.name for path in package.iterdir()} & RETIRED_MODULES


def test_artifact_enum_is_research_only():
    assert {item.value for item in ArtifactType} == {
        "MODEL_SCORE",
        "MODEL_TOPK",
        "STRATEGY_DECISION",
        "TARGET_PORTFOLIO",
    }
