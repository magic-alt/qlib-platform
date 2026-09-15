from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from qlib_platform.research.contracts.cross_market import (
    CrossMarketObservation,
    FxQuote,
    MarketSessionEvidence,
    cross_market_valuation_rates,
    resolve_fx_rate,
    select_cross_market_observations,
)


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_market_close_evidence_preserves_new_york_dst_shift() -> None:
    shanghai = MarketSessionEvidence(
        "CN",
        "cn_stock_v1",
        date(2026, 3, 9),
        "Asia/Shanghai",
        time(15, 0),
    )
    new_york_before_dst = MarketSessionEvidence(
        "US",
        "us_equity_fixture_v1",
        date(2026, 3, 6),
        "America/New_York",
        time(16, 0),
    )
    new_york_after_dst = MarketSessionEvidence(
        "US",
        "us_equity_fixture_v1",
        date(2026, 3, 9),
        "America/New_York",
        time(16, 0),
    )

    assert shanghai.close_at().astimezone(timezone.utc) == _utc("2026-03-09T07:00:00Z")
    assert new_york_before_dst.close_at().astimezone(timezone.utc) == _utc(
        "2026-03-06T21:00:00Z"
    )
    assert new_york_after_dst.close_at().astimezone(timezone.utc) == _utc(
        "2026-03-09T20:00:00Z"
    )


def test_same_session_date_does_not_leak_a_market_that_has_not_closed() -> None:
    sessions = [
        MarketSessionEvidence("CN", "cn_stock_v1", date(2026, 3, 9), "Asia/Shanghai", time(15)),
        MarketSessionEvidence(
            "US",
            "us_equity_fixture_v1",
            date(2026, 3, 9),
            "America/New_York",
            time(16),
        ),
    ]
    observations = [
        CrossMarketObservation(
            "CN.XSHG.EQUITY.600000",
            "CN",
            "cn_stock_v1",
            "CNY",
            date(2026, 3, 9),
            _utc("2026-03-09T07:05:00Z"),
        ),
        CrossMarketObservation(
            "US.XNAS.EQUITY.AAPL",
            "US",
            "us_equity_fixture_v1",
            "USD",
            date(2026, 3, 9),
            _utc("2026-03-09T20:05:00Z"),
        ),
    ]

    selected = select_cross_market_observations(
        observations,
        sessions,
        as_of=_utc("2026-03-09T10:00:00Z"),
    )

    assert [item.instrument_id for item in selected.eligible] == ["CN.XSHG.EQUITY.600000"]
    assert selected.excluded[("US.XNAS.EQUITY.AAPL", date(2026, 3, 9))] == (
        "future_available_at"
    )


def test_market_holidays_are_evidenced_per_calendar_not_by_shared_dates() -> None:
    sessions = [
        MarketSessionEvidence("CN", "cn_stock_v1", date(2026, 4, 1), "Asia/Shanghai", time(15)),
        MarketSessionEvidence(
            "US",
            "us_equity_fixture_v1",
            date(2026, 4, 1),
            "America/New_York",
            time(16),
            is_open=False,
        ),
        MarketSessionEvidence(
            "CN",
            "cn_stock_v1",
            date(2026, 4, 2),
            "Asia/Shanghai",
            time(15),
            is_open=False,
        ),
        MarketSessionEvidence(
            "US",
            "us_equity_fixture_v1",
            date(2026, 4, 2),
            "America/New_York",
            time(16),
        ),
    ]
    observations = [
        CrossMarketObservation(
            "CN.XSHG.EQUITY.600000",
            "CN",
            "cn_stock_v1",
            "CNY",
            date(2026, 4, 1),
            _utc("2026-04-01T07:05:00Z"),
        ),
        CrossMarketObservation(
            "US.XNAS.EQUITY.AAPL",
            "US",
            "us_equity_fixture_v1",
            "USD",
            date(2026, 4, 1),
            _utc("2026-04-01T20:05:00Z"),
        ),
        CrossMarketObservation(
            "CN.XSHG.EQUITY.600000",
            "CN",
            "cn_stock_v1",
            "CNY",
            date(2026, 4, 2),
            _utc("2026-04-02T07:05:00Z"),
        ),
        CrossMarketObservation(
            "US.XNAS.EQUITY.AAPL",
            "US",
            "us_equity_fixture_v1",
            "USD",
            date(2026, 4, 2),
            _utc("2026-04-02T20:05:00Z"),
        ),
    ]

    selected = select_cross_market_observations(
        observations,
        sessions,
        as_of=_utc("2026-04-03T00:00:00Z"),
    )

    assert {(item.instrument_id, item.session_date) for item in selected.eligible} == {
        ("CN.XSHG.EQUITY.600000", date(2026, 4, 1)),
        ("US.XNAS.EQUITY.AAPL", date(2026, 4, 2)),
    }
    assert selected.excluded[("US.XNAS.EQUITY.AAPL", date(2026, 4, 1))] == "market_closed"
    assert selected.excluded[("CN.XSHG.EQUITY.600000", date(2026, 4, 2))] == "market_closed"


def test_cross_market_evidence_fails_closed_on_missing_or_ambiguous_timing() -> None:
    session = MarketSessionEvidence(
        "CN",
        "cn_stock_v1",
        date(2026, 3, 9),
        "Asia/Shanghai",
        time(15),
    )
    missing_calendar = CrossMarketObservation(
        "US.XNAS.EQUITY.AAPL",
        "US",
        "us_equity_fixture_v1",
        "USD",
        date(2026, 3, 9),
        _utc("2026-03-09T20:05:00Z"),
    )
    selected = select_cross_market_observations(
        [missing_calendar],
        [session],
        as_of=_utc("2026-03-10T00:00:00Z"),
    )
    assert selected.excluded[missing_calendar.key] == "missing_calendar_session"

    too_early = CrossMarketObservation(
        "CN.XSHG.EQUITY.600000",
        "CN",
        "cn_stock_v1",
        "CNY",
        date(2026, 3, 9),
        _utc("2026-03-09T06:59:00Z"),
    )
    with pytest.raises(ValueError, match="precedes its certified session close"):
        select_cross_market_observations(
            [too_early],
            [session],
            as_of=_utc("2026-03-10T00:00:00Z"),
        )
    with pytest.raises(ValueError, match="duplicate market session"):
        select_cross_market_observations(
            [],
            [session, session],
            as_of=_utc("2026-03-10T00:00:00Z"),
        )

    valid = CrossMarketObservation(
        "CN.XSHG.EQUITY.600000",
        "CN",
        "cn_stock_v1",
        "CNY",
        date(2026, 3, 9),
        _utc("2026-03-09T07:05:00Z"),
    )
    with pytest.raises(ValueError, match="duplicate cross-market observation"):
        select_cross_market_observations(
            [valid, valid],
            [session],
            as_of=_utc("2026-03-10T00:00:00Z"),
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        select_cross_market_observations([], [], as_of=datetime(2026, 3, 10))


def test_fx_resolution_requires_direct_fresh_as_of_evidence() -> None:
    quotes = [
        FxQuote("USD", "CNY", 7.1, _utc("2026-03-09T09:00:00Z")),
        FxQuote("USD", "CNY", 7.2, _utc("2026-03-09T11:00:00Z")),
    ]

    assert resolve_fx_rate(
        "USD",
        "CNY",
        quotes,
        as_of=_utc("2026-03-09T10:00:00Z"),
    ) == pytest.approx(7.1)
    assert resolve_fx_rate(
        "CNY",
        "CNY",
        quotes,
        as_of=_utc("2026-03-09T10:00:00Z"),
    ) == 1.0
    with pytest.raises(ValueError, match="unknown FX conversion"):
        resolve_fx_rate(
            "HKD",
            "CNY",
            quotes,
            as_of=_utc("2026-03-09T10:00:00Z"),
        )
    with pytest.raises(ValueError, match="no as-of FX"):
        resolve_fx_rate(
            "USD",
            "CNY",
            [FxQuote("USD", "CNY", 7.2, _utc("2026-03-09T11:00:00Z"))],
            as_of=_utc("2026-03-09T10:00:00Z"),
        )
    with pytest.raises(ValueError, match="stale FX"):
        resolve_fx_rate(
            "USD",
            "CNY",
            [FxQuote("USD", "CNY", 7.0, _utc("2026-03-01T00:00:00Z"))],
            as_of=_utc("2026-03-09T10:00:00Z"),
            max_age=timedelta(days=2),
        )
    with pytest.raises(ValueError, match="FX max_age"):
        resolve_fx_rate(
            "USD",
            "CNY",
            quotes,
            as_of=_utc("2026-03-09T10:00:00Z"),
            max_age=timedelta(0),
        )


def test_fx_conflicts_invalid_quotes_and_cross_currency_valuation_fail_closed() -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        FxQuote("USD", "CNY", float("nan"), _utc("2026-03-09T09:00:00Z"))
    with pytest.raises(ValueError, match="must differ"):
        FxQuote("USD", "USD", 1.0, _utc("2026-03-09T09:00:00Z"))
    with pytest.raises(ValueError, match="timezone-aware"):
        FxQuote("USD", "CNY", 7.1, datetime(2026, 3, 9, 9))

    conflicting = [
        FxQuote("USD", "CNY", 7.1, _utc("2026-03-09T09:00:00Z")),
        FxQuote("USD", "CNY", 7.2, _utc("2026-03-09T09:00:00Z")),
    ]
    with pytest.raises(ValueError, match="conflicting FX quotes"):
        resolve_fx_rate(
            "USD",
            "CNY",
            conflicting,
            as_of=_utc("2026-03-09T10:00:00Z"),
        )

    observations = [
        CrossMarketObservation(
            "CN.XSHG.EQUITY.600000",
            "CN",
            "cn_stock_v1",
            "CNY",
            date(2026, 3, 9),
            _utc("2026-03-09T07:05:00Z"),
        ),
        CrossMarketObservation(
            "US.XNAS.EQUITY.AAPL",
            "US",
            "us_equity_fixture_v1",
            "USD",
            date(2026, 3, 9),
            _utc("2026-03-09T20:05:00Z"),
        ),
    ]
    with pytest.raises(ValueError, match="unknown FX conversion"):
        cross_market_valuation_rates(
            observations,
            [],
            valuation_currency="CNY",
            as_of=_utc("2026-03-10T00:00:00Z"),
        )

    rates = cross_market_valuation_rates(
        observations,
        [FxQuote("USD", "CNY", 7.1, _utc("2026-03-09T21:00:00Z"))],
        valuation_currency="CNY",
        as_of=_utc("2026-03-10T00:00:00Z"),
    )
    assert rates["CN.XSHG.EQUITY.600000"] == 1.0
    assert rates["US.XNAS.EQUITY.AAPL"] == pytest.approx(7.1)


def test_cross_market_contract_validates_timezones_and_currency_identity() -> None:
    with pytest.raises(ValueError, match="unknown market timezone"):
        MarketSessionEvidence("US", "fixture", date(2026, 3, 9), "Mars/Olympus", time(16))
    with pytest.raises(ValueError, match="without tzinfo"):
        MarketSessionEvidence(
            "US",
            "fixture",
            date(2026, 3, 9),
            "America/New_York",
            time(16, tzinfo=timezone.utc),
        )
    with pytest.raises(ValueError, match="currency is required"):
        CrossMarketObservation(
            "US.XNAS.EQUITY.AAPL",
            "US",
            "fixture",
            "",
            date(2026, 3, 9),
            _utc("2026-03-09T20:05:00Z"),
        )
