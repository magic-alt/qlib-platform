from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError

import pytest

from qlib_platform.alpha.applicability import (
    alpha_pack_applicability,
    assert_alpha_pack_profile_compatible,
)
from qlib_platform.alpha.base import AlphaPackSpec
from qlib_platform.alpha.registry import ALPHA_PACKS, assert_alpha_pack_compatible
from qlib_platform.research.contracts.research_profile import (
    CalendarVersion,
    InstrumentSpec,
    LabelDefinition,
    ResearchProfile,
    ashare_etf_instrument,
    assert_information_available,
    assert_settings_profile_compatible,
    describe_research_profile,
    legacy_ashare_instrument,
    require_research_profile,
    research_profile_from_settings,
)


class _Settings:
    def __init__(self, tmp_path: Path, data: dict[str, object]) -> None:
        self.data = data
        self.qlib_data_uri = tmp_path / "qlib"
        self.platform_release_manifest = tmp_path / "release.json"

    def uses_platform_release(self) -> bool:
        return False


def test_legacy_ashare_identity_is_vendor_neutral_and_handoff_ready() -> None:
    from_qlib = legacy_ashare_instrument("SH600000")
    from_tushare = legacy_ashare_instrument("600000.SH")

    assert from_qlib == from_tushare
    assert from_qlib.instrument_id == "CN.XSHG.EQUITY.600000"
    assert from_qlib.currency == "CNY"
    assert [alias.to_manifest() for alias in from_qlib.aliases] == [
        {"provider": "qlib", "symbol": "SH600000", "valid_from": None, "valid_to": None},
        {"provider": "tushare", "symbol": "600000.SH", "valid_from": None, "valid_to": None},
    ]
    manifest = from_qlib.to_manifest()
    assert manifest["venue"] == "XSHG"
    assert manifest["aliases"][1]["symbol"] == "600000.SH"
    from_qlib.assert_governed_handoff_ready()


def test_ashare_etf_identity_is_vendor_neutral_and_distinct_from_stock_identity() -> None:
    from_qlib = ashare_etf_instrument("SH510300")
    from_tushare = ashare_etf_instrument("510300.SH")

    assert from_qlib == from_tushare
    assert from_qlib.instrument_id == "CN.XSHG.ETF.510300"
    assert from_qlib.subtype == "etf"
    assert from_qlib.instrument_id != legacy_ashare_instrument("SH510300").instrument_id
    assert [alias.symbol for alias in from_qlib.aliases] == ["SH510300", "510300.SH"]
    from_qlib.assert_governed_handoff_ready()

    with pytest.raises(ValueError, match="venue is not supported"):
        ashare_etf_instrument("BJ510300")


def test_instrument_handoff_rejects_research_only_continuous_and_incomplete_derivatives() -> None:
    common = legacy_ashare_instrument("SZ000001")
    with pytest.raises(ValueError, match="research-only"):
        replace(common, tradable=False).assert_governed_handoff_ready()
    with pytest.raises(ValueError, match="continuous research series"):
        replace(common, continuous_series=True).assert_governed_handoff_ready()
    with pytest.raises(ValueError, match="currency is required"):
        replace(common, currency="").assert_governed_handoff_ready()

    future = InstrumentSpec(
        instrument_id="CN.XSGE.FUTURE.RB9999",
        asset_class="future",
        subtype="continuous_future",
        market="CN",
        venue="XSGE",
        currency="CNY",
        calendar_id="cn_future_v1",
        tradable=True,
    )
    with pytest.raises(ValueError, match="missing=.*multiplier"):
        future.assert_governed_handoff_ready()

    tradable_future = replace(
        future,
        subtype="future_contract",
        underlying_id="CN.XSGE.FUTURE_ROOT.RB",
        expiry="2026-10",
        multiplier="10",
    )
    tradable_future.assert_governed_handoff_ready()


def test_calendar_label_and_profile_contracts_are_versioned_and_fail_closed() -> None:
    calendar = CalendarVersion("fixture_calendar", 1, "Asia/Shanghai", "15:00:00")
    assert len(calendar.fingerprint) == 64
    with pytest.raises(ZoneInfoNotFoundError):
        CalendarVersion("bad", 1, "Not/AZone", "15:00:00")

    label = LabelDefinition("return_5d_t1_v1", 1, 5, 1, "close")
    with pytest.raises(ValueError, match="horizon_sessions"):
        LabelDefinition("bad", 1, 0, 1, "close")
    with pytest.raises(ValueError, match="execution_lag_sessions"):
        LabelDefinition("bad", 1, 5, -1, "close")

    profile = ResearchProfile(
        profile_id="fixture_v1",
        version=1,
        asset_class="equity",
        subtype="common_stock",
        market="CN",
        currency="CNY",
        calendar=calendar,
        frequency="day",
        adjustment="factor_adjusted",
        price_field="close",
        label=label,
        allowed_workflows=("research",),
    )
    assert profile.to_manifest()["schema_version"] == 1
    assert len(profile.fingerprint) == 64
    profile.assert_workflow_allowed("research")
    with pytest.raises(ValueError, match="not allowed"):
        profile.assert_workflow_allowed("backtest")
    with pytest.raises(ValueError, match="unsupported workflows"):
        replace(profile, allowed_workflows=("research", "live"))
    with pytest.raises(ValueError, match="research_only"):
        replace(profile, promotion_scope="paper")


def test_profile_registry_defaults_to_legacy_ashare_and_enables_explicit_etf(tmp_path) -> None:
    settings = _Settings(tmp_path, {})
    profile = research_profile_from_settings(settings)
    assert profile.profile_id == "ashare_equity_v1"
    assert profile.subtype == "common_stock"
    assert profile.promotion_scope == "research_only"
    assert require_research_profile("ashare_equity_v1") is profile

    etf = require_research_profile("ashare_etf_v1")
    assert etf is describe_research_profile("ashare_etf_v1")
    assert etf.implemented is True
    assert etf.subtype == "etf"
    assert etf.promotion_scope == "research_only"

    with pytest.raises(ValueError, match="unknown research profile"):
        describe_research_profile("future_option_v1")

    configured = _Settings(tmp_path, {"experiment": {"research_profile": "ashare_etf_v1"}})
    assert research_profile_from_settings(configured) is etf


def test_settings_preflight_rejects_label_frequency_and_horizon_drift(tmp_path) -> None:
    profile = require_research_profile("ashare_equity_v1")
    good = _Settings(
        tmp_path,
        {
            "experiment": {"label": {"spec": "return_5d_t1_v1"}},
            "qlib": {"frequency": "day"},
            "research": {"label_horizon_days": 5},
        },
    )
    assert_settings_profile_compatible(good, profile)

    bad_label = _Settings(tmp_path, {"experiment": {"label": {"spec": "return_1d_v1"}}})
    with pytest.raises(ValueError, match="requires label"):
        assert_settings_profile_compatible(bad_label, profile)

    bad_frequency = _Settings(tmp_path, {"qlib": {"frequency": "1min"}})
    with pytest.raises(ValueError, match="requires frequency"):
        assert_settings_profile_compatible(bad_frequency, profile)

    bad_horizon = _Settings(tmp_path, {"research": {"label_horizon_days": 10}})
    with pytest.raises(ValueError, match="requires label horizon"):
        assert_settings_profile_compatible(bad_horizon, profile)


def test_alpha_applicability_is_separate_from_frozen_alpha_pack_identity(tmp_path) -> None:
    for pack in ALPHA_PACKS.values():
        before = pack.to_manifest()
        descriptor = alpha_pack_applicability(pack)
        assert descriptor.pack_id == pack.pack_id
        assert descriptor.required_fields == pack.required_qlib_fields
        assert descriptor.required_release_components == pack.required_release_components
        assert descriptor.minimum_lookback_sessions == pack.warmup_trading_days
        assert descriptor.missing_policy == "reject_required_fields"
        assert len(descriptor.fingerprint) == 64
        assert pack.to_manifest() == before

    pit = alpha_pack_applicability(ALPHA_PACKS["alpha158_pit_v1"])
    market = alpha_pack_applicability(ALPHA_PACKS["alpha158_market_v1"])
    etf = alpha_pack_applicability(ALPHA_PACKS["etf_core_v1"])
    assert pit.pit_required is True
    assert market.pit_required is False
    assert etf.pit_required is False
    assert etf.supported_profile_ids == ("ashare_etf_v1",)

    unknown = AlphaPackSpec("future_pack", 1, "Future", (), (), 1, "future", ())
    with pytest.raises(ValueError, match="no ResearchProfile applicability"):
        alpha_pack_applicability(unknown)

    settings = _Settings(tmp_path, {})
    assert_alpha_pack_compatible(settings, ALPHA_PACKS["alpha158_market_v1"])


def test_alpha_profile_compatibility_rejects_cross_asset_pack_reuse() -> None:
    stock_pack = ALPHA_PACKS["alpha158_market_v1"]
    stock_profile = require_research_profile("ashare_equity_v1")
    etf_pack = ALPHA_PACKS["etf_core_v1"]
    etf_profile = require_research_profile("ashare_etf_v1")
    assert_alpha_pack_profile_compatible(stock_pack, stock_profile)
    assert_alpha_pack_profile_compatible(etf_pack, etf_profile)

    with pytest.raises(ValueError, match="not compatible"):
        assert_alpha_pack_profile_compatible(stock_pack, etf_profile)
    with pytest.raises(ValueError, match="not compatible"):
        assert_alpha_pack_profile_compatible(etf_pack, stock_profile)
    with pytest.raises(ValueError, match="asset class"):
        assert_alpha_pack_profile_compatible(stock_pack, replace(stock_profile, asset_class="future"))
    with pytest.raises(ValueError, match="subtype"):
        assert_alpha_pack_profile_compatible(stock_pack, replace(stock_profile, subtype="preferred_stock"))
    other_label = replace(stock_profile.label, label_id="return_1d_v1")
    with pytest.raises(ValueError, match="does not support label"):
        assert_alpha_pack_profile_compatible(stock_pack, replace(stock_profile, label=other_label))


def test_available_at_guard_uses_absolute_time_not_same_date_alignment() -> None:
    as_of = datetime(2026, 9, 15, 7, 0, tzinfo=timezone.utc)
    assert_information_available(
        available_at=datetime(2026, 9, 15, 6, 59, tzinfo=timezone.utc),
        as_of=as_of,
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        assert_information_available(
            available_at=datetime(2026, 9, 15, 6, 59),
            as_of=as_of,
        )
    with pytest.raises(ValueError, match="future available_at"):
        assert_information_available(
            available_at=datetime(2026, 9, 15, 7, 1, tzinfo=timezone.utc),
            as_of=as_of,
        )
