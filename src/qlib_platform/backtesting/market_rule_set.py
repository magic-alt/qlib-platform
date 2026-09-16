from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

import pandas as pd

from qlib_platform.lineage import sha256_json


@dataclass(frozen=True)
class RuleEvidence:
    rule_id: str
    effective_from: str | None
    effective_to: str | None
    source: str
    description: str
    fixture: str

    def contains(self, trade_date: object) -> bool:
        date = pd.Timestamp(trade_date).normalize()
        start = pd.Timestamp(self.effective_from).normalize() if self.effective_from else None
        end = pd.Timestamp(self.effective_to).normalize() if self.effective_to else None
        return (start is None or date >= start) and (end is None or date <= end)


@dataclass(frozen=True)
class FeeRegime:
    regime_id: str
    effective_from: str
    effective_to: str | None
    sell_stamp_tax_bps: float
    transfer_fee_bps: float
    regulatory_fee_bps: float
    exchange_handling_fee_bps: float
    source: str
    fixture: str

    def contains(self, trade_date: object) -> bool:
        date = pd.Timestamp(trade_date).normalize()
        start = pd.Timestamp(self.effective_from).normalize()
        end = pd.Timestamp(self.effective_to).normalize() if self.effective_to else None
        return date >= start and (end is None or date <= end)


@dataclass(frozen=True)
class MarketRuleSet:
    market_rule_set_id: str
    profile_id: str
    cost_model_id: str
    fill_model_id: str
    price_basis: str
    corporate_action_mode: str
    rules: tuple[RuleEvidence, ...]
    fee_regimes: tuple[FeeRegime, ...]

    @property
    def fingerprint(self) -> str:
        return sha256_json(self.to_manifest())

    def fee_regime_for(self, trade_date: object) -> FeeRegime:
        matches = [regime for regime in self.fee_regimes if regime.contains(trade_date)]
        if len(matches) != 1:
            date = pd.Timestamp(trade_date).strftime("%Y-%m-%d")
            raise ValueError(
                f"market rule set {self.market_rule_set_id} has no unique fee regime for {date}"
            )
        return matches[0]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "marketRuleSetId": self.market_rule_set_id,
            "profileId": self.profile_id,
            "costModelId": self.cost_model_id,
            "fillModelId": self.fill_model_id,
            "priceBasis": self.price_basis,
            "corporateActionMode": self.corporate_action_mode,
            "rules": [asdict(rule) for rule in self.rules],
            "feeRegimes": [asdict(regime) for regime in self.fee_regimes],
        }


def production_realism_rule_set() -> MarketRuleSet:
    return MarketRuleSet(
        market_rule_set_id="cn_cash_equity_production_realism_v1",
        profile_id="production_realism_v1",
        cost_model_id="cn_cash_equity_fee_schedule_v1",
        fill_model_id="conservative_daily_bar_v1",
        price_basis="raw_unadjusted",
        corporate_action_mode="cash_share_ledger_v1",
        rules=(
            RuleEvidence(
                "t_plus_one_cash_equity_v1",
                None,
                None,
                "SSE/SZSE cash-equity trading and settlement rules",
                "Newly purchased cash-equity inventory is not sellable in the same trading session.",
                "tests/test_ashare_market_realism_conformance.py::test_t_plus_one_and_sellable_quantity_ledger",
            ),
            RuleEvidence(
                "board_lot_main_chinext_bse_v1",
                None,
                None,
                "SSE/SZSE/BSE trading rules",
                "Main board, ChiNext and BSE buys use 100-share minimum lots; odd-lot sells are explicit.",
                "tests/test_ashare_market_realism_conformance.py::test_board_lot_and_odd_lot_sell_semantics",
            ),
            RuleEvidence(
                "board_lot_star_v1",
                "2019-07-22",
                None,
                "SSE STAR Market investor education / trading rules",
                "STAR orders require at least 200 shares and then permit one-share increments; a balance below 200 is sold in full.",
                "tests/test_ashare_market_realism_conformance.py::test_star_odd_lot_tail_is_sold_in_full",
            ),
            RuleEvidence(
                "chinext_20pct_v1",
                "2020-08-24",
                None,
                "SZSE ChiNext reform trading rules",
                "ChiNext price limits are 20%; IPO first five sessions are not subject to daily price limits.",
                "tests/test_ashare_market_realism_conformance.py::test_price_limit_regime_boundaries",
            ),
            RuleEvidence(
                "main_registration_ipo_v1",
                "2023-04-10",
                None,
                "SSE/SZSE main-board registration reform trading rules",
                "Main-board IPO first five trading sessions have no daily price limit from the registration-reform launch.",
                "tests/test_ashare_market_realism_conformance.py::test_price_limit_regime_boundaries",
            ),
            RuleEvidence(
                "main_st_limit_legacy_v1",
                None,
                "2026-07-05",
                "SSE/SZSE risk-warning trading rules before the 2026 revision",
                "Main-board risk-warning stocks use a 5% daily price limit before the 2026 rule change.",
                "tests/test_ashare_market_realism_conformance.py::test_price_limit_regime_boundaries",
            ),
            RuleEvidence(
                "main_st_limit_2026_v1",
                "2026-07-06",
                None,
                "SSE/SZSE 2026 trading-rule revision",
                "Main-board risk-warning stocks use a 10% daily price limit from 2026-07-06.",
                "tests/test_ashare_market_realism_conformance.py::test_price_limit_regime_boundaries",
            ),
            RuleEvidence(
                "suspension_fail_closed_v1",
                None,
                None,
                "SSE/SZSE trading-status semantics",
                "Suspended or halted securities cannot fill; an absent bar remains distinct missing provider evidence.",
                "tests/test_ashare_market_realism_conformance.py::test_suspension_and_missing_bar_are_distinct",
            ),
            RuleEvidence(
                "limit_fill_conservative_v1",
                None,
                None,
                "Research conservative fill assumption",
                "At a locked upper limit buys are rejected; at a locked lower limit sells are rejected; the opposite directions remain eligible.",
                "tests/test_ashare_market_realism_conformance.py::test_limit_fillability_is_directional",
            ),
            RuleEvidence(
                "corporate_action_raw_price_v1",
                None,
                None,
                "Platform research accounting contract",
                "Cash/share corporate actions are applied only to raw-price NAV; adjusted-price inputs fail closed to prevent double counting.",
                "tests/test_ashare_market_realism_conformance.py::test_corporate_action_nav_continuity_and_adjusted_price_guard",
            ),
            RuleEvidence(
                "listing_lifecycle_fail_closed_v1",
                None,
                None,
                "PIT security lifecycle contract",
                "Pre-listing and delisted securities are not tradable even when a provider row exists.",
                "tests/test_ashare_market_realism_conformance.py::test_listing_lifecycle_rejects_prelisting_and_delisted_rows",
            ),
        ),
        fee_regimes=(
            FeeRegime(
                "cn_equity_fee_2015_08_01",
                "2015-08-01",
                "2022-04-28",
                10.0,
                0.2,
                0.2,
                0.487,
                "ChinaClear transfer-fee schedule; CSRC regulatory fee 0.002%; SSE/SZSE A-share handling fee 0.00487%; sell stamp tax 0.1%",
                "tests/test_ashare_market_realism_conformance.py::test_versioned_fee_boundaries",
            ),
            FeeRegime(
                "cn_equity_fee_2022_04_29",
                "2022-04-29",
                "2023-08-27",
                10.0,
                0.1,
                0.2,
                0.487,
                "ChinaClear transfer-fee reduction effective 2022-04-29; CSRC regulatory fee 0.002%; SSE/SZSE handling fee 0.00487%; sell stamp tax 0.1%",
                "tests/test_ashare_market_realism_conformance.py::test_versioned_fee_boundaries",
            ),
            FeeRegime(
                "cn_equity_fee_2023_08_28",
                "2023-08-28",
                None,
                5.0,
                0.1,
                0.2,
                0.341,
                "MOF/STA Announcement No.39 (2023); ChinaClear transfer-fee schedule; CSRC regulatory fee 0.002%; SSE/SZSE handling fee reduced to 0.00341%",
                "tests/test_ashare_market_realism_conformance.py::test_versioned_fee_boundaries",
            ),
        ),
    )


def qlib_official_parity_rule_set() -> MarketRuleSet:
    return MarketRuleSet(
        market_rule_set_id="qlib_official_parity_v1",
        profile_id="official_parity_v1",
        cost_model_id="qlib_exchange_config_cost_v1",
        fill_model_id="qlib_exchange_daily_v0.9.7",
        price_basis="qlib_provider_semantics",
        corporate_action_mode="qlib_adjusted_price_semantics",
        rules=(),
        fee_regimes=(),
    )


def resolve_market_rule_set(profile: str | None) -> MarketRuleSet:
    normalized = str(profile or "production_realism_v1").strip().lower().replace("-", "_")
    if normalized in {"production_realism", "production_realism_v1", "cn_cash_equity_production_realism_v1"}:
        return production_realism_rule_set()
    if normalized in {"official_parity", "official_parity_v1", "qlib_official_parity_v1"}:
        return qlib_official_parity_rule_set()
    raise ValueError(f"unknown A-share market rule profile: {profile}")


def execution_contract_from_mapping(
    research: Mapping[str, object] | None,
    *,
    default_profile: str = "qlib_official_parity_v1",
    execution_engine_id: str = "unbound_execution_contract_v1",
) -> dict[str, Any]:
    cfg = research if isinstance(research, Mapping) else {}
    rule_set = resolve_market_rule_set(str(cfg.get("market_rule_profile") or default_profile))
    engine_id = str(execution_engine_id).strip()
    if not engine_id:
        raise ValueError("execution_engine_id is required")
    if rule_set.profile_id == "production_realism_v1" and engine_id not in {
        "unbound_execution_contract_v1",
        "deterministic_ashare_simulator_v1",
    }:
        raise ValueError(
            "production_realism_v1 cannot be labeled on a Qlib generic Exchange run; "
            "use deterministic_ashare_simulator_v1 or keep qlib_official_parity_v1"
        )
    broker_cost = {
        "commissionBps": float(cfg.get("commission_bps", 1.0)),
        "minCommission": float(cfg.get("min_commission", cfg.get("min_cost", 5.0))),
        "qlibOpenCost": float(cfg.get("open_cost", 0.00035)),
        "qlibCloseCost": float(cfg.get("close_cost", 0.00085)),
    }
    payload = {
        **rule_set.to_manifest(),
        "marketRuleSetSha256": rule_set.fingerprint,
        "executionEngineId": engine_id,
        "brokerCost": broker_cost,
        "dealPrice": str(cfg.get("deal_price", "open")),
        "tradeUnit": int(cfg.get("trade_unit", 100)),
        "maxParticipationRate": float(cfg.get("max_participation_rate", 0.05)),
    }
    payload["executionContractSha256"] = sha256_json(payload)
    return payload
