from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from math import isfinite
from typing import Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from qlib_platform.research.contracts.research_profile import assert_information_available


def _require_text(value: str, label: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    return normalized


def _require_aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value


@dataclass(frozen=True)
class MarketSessionEvidence:
    market: str
    calendar_id: str
    session_date: date
    timezone_name: str
    session_close: time
    is_open: bool = True

    def __post_init__(self) -> None:
        _require_text(self.market, "market")
        _require_text(self.calendar_id, "calendar_id")
        if self.session_close.tzinfo is not None:
            raise ValueError("session_close must be a local wall-clock time without tzinfo")
        try:
            ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown market timezone: {self.timezone_name}") from exc

    def close_at(self) -> datetime:
        return datetime.combine(
            self.session_date,
            self.session_close,
            tzinfo=ZoneInfo(self.timezone_name),
        )

    @property
    def key(self) -> tuple[str, str, date]:
        return (self.market, self.calendar_id, self.session_date)


@dataclass(frozen=True)
class CrossMarketObservation:
    instrument_id: str
    market: str
    calendar_id: str
    currency: str
    session_date: date
    available_at: datetime

    def __post_init__(self) -> None:
        _require_text(self.instrument_id, "instrument_id")
        _require_text(self.market, "market")
        _require_text(self.calendar_id, "calendar_id")
        _require_text(self.currency, "currency")
        _require_aware(self.available_at, "available_at")

    @property
    def key(self) -> tuple[str, date]:
        return (self.instrument_id, self.session_date)


@dataclass(frozen=True)
class CrossMarketSelection:
    eligible: tuple[CrossMarketObservation, ...]
    excluded: dict[tuple[str, date], str]


@dataclass(frozen=True)
class FxQuote:
    base_currency: str
    quote_currency: str
    rate: float
    available_at: datetime

    def __post_init__(self) -> None:
        _require_text(self.base_currency, "base_currency")
        _require_text(self.quote_currency, "quote_currency")
        if self.base_currency.upper() == self.quote_currency.upper():
            raise ValueError("FX quote currencies must differ")
        if not isfinite(self.rate) or self.rate <= 0:
            raise ValueError("FX rate must be finite and positive")
        _require_aware(self.available_at, "FX available_at")


def select_cross_market_observations(
    observations: Iterable[CrossMarketObservation],
    sessions: Iterable[MarketSessionEvidence],
    *,
    as_of: datetime,
) -> CrossMarketSelection:
    """Select only observations whose own market session and availability are known as of ``as_of``."""

    _require_aware(as_of, "as_of")
    session_map: dict[tuple[str, str, date], MarketSessionEvidence] = {}
    for session in sessions:
        if session.key in session_map:
            raise ValueError(f"duplicate market session evidence: {session.key}")
        session_map[session.key] = session

    seen: set[tuple[str, date]] = set()
    eligible: list[CrossMarketObservation] = []
    excluded: dict[tuple[str, date], str] = {}
    for observation in observations:
        if observation.key in seen:
            raise ValueError(f"duplicate cross-market observation: {observation.key}")
        seen.add(observation.key)
        session_key = (
            observation.market,
            observation.calendar_id,
            observation.session_date,
        )
        matched_session = session_map.get(session_key)
        if matched_session is None:
            excluded[observation.key] = "missing_calendar_session"
            continue
        if not matched_session.is_open:
            excluded[observation.key] = "market_closed"
            continue
        if observation.available_at < matched_session.close_at():
            raise ValueError(
                "cross-market observation available_at precedes its certified session close: "
                f"{observation.instrument_id} {observation.session_date}"
            )
        if observation.available_at > as_of:
            excluded[observation.key] = "future_available_at"
            continue
        assert_information_available(available_at=observation.available_at, as_of=as_of)
        eligible.append(observation)

    return CrossMarketSelection(
        tuple(sorted(eligible, key=lambda item: (item.available_at, item.instrument_id))),
        excluded,
    )


def resolve_fx_rate(
    base_currency: str,
    quote_currency: str,
    quotes: Iterable[FxQuote],
    *,
    as_of: datetime,
    max_age: timedelta = timedelta(days=2),
) -> float:
    """Resolve one direct, fresh FX quote without inferring or backfilling an unknown conversion."""

    base = _require_text(base_currency, "base_currency").upper()
    quote = _require_text(quote_currency, "quote_currency").upper()
    _require_aware(as_of, "as_of")
    if max_age <= timedelta(0):
        raise ValueError("FX max_age must be positive")
    if base == quote:
        return 1.0

    pair_quotes = [
        item for item in quotes if item.base_currency.upper() == base and item.quote_currency.upper() == quote
    ]
    if not pair_quotes:
        raise ValueError(f"unknown FX conversion: {base}/{quote}")

    usable = [item for item in pair_quotes if item.available_at <= as_of]
    if not usable:
        raise ValueError(f"no as-of FX quote is available for {base}/{quote}")
    latest_at = max(item.available_at for item in usable)
    latest = [item for item in usable if item.available_at == latest_at]
    rates = {item.rate for item in latest}
    if len(rates) != 1:
        raise ValueError(f"conflicting FX quotes at {latest_at.isoformat()} for {base}/{quote}")
    assert_information_available(available_at=latest_at, as_of=as_of)
    if as_of.astimezone(timezone.utc) - latest_at.astimezone(timezone.utc) > max_age:
        raise ValueError(f"stale FX quote for {base}/{quote}")
    return latest[0].rate


def cross_market_valuation_rates(
    observations: Iterable[CrossMarketObservation],
    quotes: Iterable[FxQuote],
    *,
    valuation_currency: str,
    as_of: datetime,
    max_fx_age: timedelta = timedelta(days=2),
) -> dict[str, float]:
    """Require an explicit fresh conversion for every observed non-base currency."""

    observations_tuple = tuple(observations)
    quotes_tuple = tuple(quotes)
    currencies_by_instrument: dict[str, str] = {}
    for observation in observations_tuple:
        existing = currencies_by_instrument.get(observation.instrument_id)
        currency = observation.currency.upper()
        if existing is not None and existing != currency:
            raise ValueError(
                f"instrument currency changed within one valuation set: {observation.instrument_id}"
            )
        currencies_by_instrument[observation.instrument_id] = currency
    return {
        instrument_id: resolve_fx_rate(
            currency,
            valuation_currency,
            quotes_tuple,
            as_of=as_of,
            max_age=max_fx_age,
        )
        for instrument_id, currency in sorted(currencies_by_instrument.items())
    }
