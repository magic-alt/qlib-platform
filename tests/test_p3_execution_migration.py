from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from tushare_qlib.cli import parser


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


def test_qmt_gateway_is_not_packaged_or_exposed_by_qlib_platform():
    root = Path(__file__).resolve().parents[1]
    metadata = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert "qmt-gateway" not in metadata
    assert "tq-qmt-gateway" not in metadata
    assert 'exclude = ["tushare_qlib.qmt_gateway*"]' in metadata
    assert (root / "src" / "tushare_qlib" / "qmt_gateway").is_dir()  # rollback-only source


def test_cli_does_not_import_frozen_execution_domains():
    root = Path(__file__).resolve().parents[1]
    source = (root / "src" / "tushare_qlib" / "cli.py").read_text(encoding="utf-8")
    forbidden = (
        "from .execution import",
        "from .risk_engine import HardRiskPolicy",
        "from .broker_state import",
        "from .holdings_ledger import",
        "from .pretrade_runner import",
        "from .production_orchestrator import",
        "from .shadow_runner import",
    )
    assert not any(token in source for token in forbidden)
