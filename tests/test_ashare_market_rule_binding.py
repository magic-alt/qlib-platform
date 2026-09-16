from __future__ import annotations

import pytest

from qlib_platform.backtesting.market_rule_set import execution_contract_from_mapping


def test_production_realism_cannot_relabel_qlib_exchange() -> None:
    with pytest.raises(ValueError, match="cannot be labeled on a Qlib generic Exchange run"):
        execution_contract_from_mapping(
            {"market_rule_profile": "production_realism_v1"},
            execution_engine_id="qlib_exchange_v0.9.7",
        )


def test_production_realism_binds_to_deterministic_simulator_identity() -> None:
    contract = execution_contract_from_mapping(
        {"market_rule_profile": "production_realism_v1"},
        execution_engine_id="deterministic_ashare_simulator_v1",
    )

    assert contract["marketRuleSetId"] == "cn_cash_equity_production_realism_v1"
    assert contract["executionEngineId"] == "deterministic_ashare_simulator_v1"
    assert contract["executionContractSha256"]
