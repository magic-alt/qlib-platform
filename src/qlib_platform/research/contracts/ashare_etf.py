from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Iterable, Mapping

from qlib_platform.lineage import sha256_json
from qlib_platform.research.contracts.research_profile import InstrumentSpec

ETF_REQUIRED_DATASETS = (
    "etf_instrument_master",
    "etf_daily",
    "etf_adjustment_factor",
    "trading_calendar",
)
ETF_FORBIDDEN_STOCK_DATASETS = (
    "equity_daily_basic",
    "pit_fundamentals",
    "industry_classification_pit",
)


@dataclass(frozen=True)
class EtfUniversePolicy:
    universe_id: str = "ashare_etf"
    version: int = 1
    allowed_venues: tuple[str, ...] = ("XSHG", "XSHE")
    exclude_suspended: bool = True
    require_positive_volume: bool = True

    @property
    def fingerprint(self) -> str:
        return sha256_json(asdict(self))


@dataclass(frozen=True)
class EtfUniverseMember:
    instrument: InstrumentSpec
    list_date: date
    delist_date: date | None = None

    def __post_init__(self) -> None:
        if self.instrument.asset_class != "equity" or self.instrument.subtype != "etf":
            raise ValueError("ETF universe members must use the equity/etf instrument contract")
        if self.delist_date is not None and self.delist_date < self.list_date:
            raise ValueError("ETF delist_date must not precede list_date")

    def active_on(self, trading_date: date) -> bool:
        return self.list_date <= trading_date and (
            self.delist_date is None or trading_date <= self.delist_date
        )


@dataclass(frozen=True)
class EtfTradabilityObservation:
    instrument_id: str
    trading_date: date
    volume: float
    suspended: bool = False

    def __post_init__(self) -> None:
        if self.volume < 0:
            raise ValueError("ETF volume must not be negative")


@dataclass(frozen=True)
class EtfUniverseSelection:
    trading_date: date
    eligible: tuple[str, ...]
    excluded: Mapping[str, str]


def assert_etf_data_contract(dataset_kinds: Iterable[str]) -> None:
    kinds = {str(kind) for kind in dataset_kinds}
    forbidden = sorted(kinds.intersection(ETF_FORBIDDEN_STOCK_DATASETS))
    if forbidden:
        raise ValueError(f"ETF research must not borrow stock-only datasets: {forbidden}")
    missing = sorted(set(ETF_REQUIRED_DATASETS) - kinds)
    if missing:
        raise ValueError(f"ETF research data contract is incomplete: missing={missing}")


def select_ashare_etf_universe(
    members: Iterable[EtfUniverseMember],
    observations: Iterable[EtfTradabilityObservation],
    *,
    trading_date: date,
    policy: EtfUniversePolicy | None = None,
) -> EtfUniverseSelection:
    resolved = policy or EtfUniversePolicy()
    states = {
        observation.instrument_id: observation
        for observation in observations
        if observation.trading_date == trading_date
    }
    eligible: list[str] = []
    excluded: dict[str, str] = {}

    for member in sorted(members, key=lambda item: item.instrument.instrument_id):
        instrument = member.instrument
        instrument_id = instrument.instrument_id
        if instrument.market != "CN" or instrument.venue not in resolved.allowed_venues:
            excluded[instrument_id] = "unsupported_market_or_venue"
            continue
        if not instrument.tradable:
            excluded[instrument_id] = "instrument_not_tradable"
            continue
        if not member.active_on(trading_date):
            excluded[instrument_id] = "outside_listing_window"
            continue
        observation = states.get(instrument_id)
        if observation is None:
            excluded[instrument_id] = "missing_tradability_observation"
            continue
        if resolved.exclude_suspended and observation.suspended:
            excluded[instrument_id] = "suspended"
            continue
        if resolved.require_positive_volume and observation.volume <= 0:
            excluded[instrument_id] = "non_positive_volume"
            continue
        eligible.append(instrument_id)

    return EtfUniverseSelection(trading_date, tuple(eligible), excluded)
