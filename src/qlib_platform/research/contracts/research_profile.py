from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Mapping
from zoneinfo import ZoneInfo

from qlib_platform.data.symbols import qlib_to_ts, ts_to_qlib
from qlib_platform.lineage import sha256_json

if TYPE_CHECKING:
    from qlib_platform.settings import Settings


DEFAULT_RESEARCH_PROFILE_ID = "ashare_equity_v1"
RESEARCH_PROFILE_SCHEMA_VERSION = 1

_ASHARE_MIC = {"SH": "XSHG", "SZ": "XSHE", "BJ": "XBSE"}
_ALLOWED_WORKFLOWS = frozenset({"research", "backtest", "diagnostics"})


@dataclass(frozen=True)
class SymbolAlias:
    provider: str
    symbol: str
    valid_from: str | None = None
    valid_to: str | None = None

    def to_manifest(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InstrumentSpec:
    instrument_id: str
    asset_class: str
    subtype: str
    market: str
    venue: str
    currency: str
    calendar_id: str
    tradable: bool
    aliases: tuple[SymbolAlias, ...] = ()
    continuous_series: bool = False
    underlying_id: str | None = None
    expiry: str | None = None
    multiplier: str | None = None

    def to_manifest(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["aliases"] = [alias.to_manifest() for alias in self.aliases]
        return payload

    def assert_governed_handoff_ready(self) -> None:
        if not self.tradable:
            raise ValueError(f"instrument is research-only and cannot be handed off: {self.instrument_id}")
        if self.continuous_series:
            raise ValueError(f"continuous research series is not orderable: {self.instrument_id}")
        if not self.currency:
            raise ValueError(f"instrument currency is required: {self.instrument_id}")
        if self.asset_class in {"future", "option"}:
            missing = [
                name
                for name, value in (
                    ("multiplier", self.multiplier),
                    ("expiry", self.expiry),
                    ("underlying_id", self.underlying_id),
                )
                if not value
            ]
            if missing:
                raise ValueError(
                    f"derivative instrument is incomplete for governed handoff: {self.instrument_id}; "
                    f"missing={missing}"
                )


@dataclass(frozen=True)
class CalendarVersion:
    calendar_id: str
    version: int
    timezone: str
    session_close_local: str

    def __post_init__(self) -> None:
        ZoneInfo(self.timezone)

    @property
    def fingerprint(self) -> str:
        return sha256_json(asdict(self))


@dataclass(frozen=True)
class LabelDefinition:
    label_id: str
    version: int
    horizon_sessions: int
    execution_lag_sessions: int
    price_field: str

    def __post_init__(self) -> None:
        if self.horizon_sessions <= 0:
            raise ValueError("label horizon_sessions must be positive")
        if self.execution_lag_sessions < 0:
            raise ValueError("label execution_lag_sessions must be non-negative")


@dataclass(frozen=True)
class ResearchProfile:
    profile_id: str
    version: int
    asset_class: str
    subtype: str
    market: str
    currency: str
    calendar: CalendarVersion
    frequency: str
    adjustment: str
    price_field: str
    label: LabelDefinition
    allowed_workflows: tuple[str, ...]
    promotion_scope: str = "research_only"
    implemented: bool = True

    def __post_init__(self) -> None:
        unsupported = set(self.allowed_workflows) - _ALLOWED_WORKFLOWS
        if unsupported:
            raise ValueError(f"research profile declares unsupported workflows: {sorted(unsupported)}")
        if self.promotion_scope != "research_only":
            raise ValueError("qlib-platform research profiles must remain research_only")

    @property
    def fingerprint(self) -> str:
        return sha256_json(self.to_manifest())

    def to_manifest(self) -> dict[str, Any]:
        return {
            "schema_version": RESEARCH_PROFILE_SCHEMA_VERSION,
            "profile_id": self.profile_id,
            "version": self.version,
            "asset_class": self.asset_class,
            "subtype": self.subtype,
            "market": self.market,
            "currency": self.currency,
            "calendar": {**asdict(self.calendar), "fingerprint": self.calendar.fingerprint},
            "frequency": self.frequency,
            "adjustment": self.adjustment,
            "price_field": self.price_field,
            "label": asdict(self.label),
            "allowed_workflows": list(self.allowed_workflows),
            "promotion_scope": self.promotion_scope,
            "implemented": self.implemented,
        }

    def assert_workflow_allowed(self, workflow: str) -> None:
        if workflow not in self.allowed_workflows:
            raise ValueError(f"workflow {workflow!r} is not allowed by research profile {self.profile_id}")


_CN_STOCK_CALENDAR_V1 = CalendarVersion(
    calendar_id="cn_stock_v1",
    version=1,
    timezone="Asia/Shanghai",
    session_close_local="15:00:00",
)
_RETURN_5D_T1_V1 = LabelDefinition(
    label_id="return_5d_t1_v1",
    version=1,
    horizon_sessions=5,
    execution_lag_sessions=1,
    price_field="close",
)

RESEARCH_PROFILES: dict[str, ResearchProfile] = {
    DEFAULT_RESEARCH_PROFILE_ID: ResearchProfile(
        profile_id=DEFAULT_RESEARCH_PROFILE_ID,
        version=1,
        asset_class="equity",
        subtype="common_stock",
        market="CN",
        currency="CNY",
        calendar=_CN_STOCK_CALENDAR_V1,
        frequency="day",
        adjustment="factor_adjusted",
        price_field="close",
        label=_RETURN_5D_T1_V1,
        allowed_workflows=("research", "backtest", "diagnostics"),
    ),
    "ashare_etf_v1": ResearchProfile(
        profile_id="ashare_etf_v1",
        version=1,
        asset_class="equity",
        subtype="etf",
        market="CN",
        currency="CNY",
        calendar=_CN_STOCK_CALENDAR_V1,
        frequency="day",
        adjustment="factor_adjusted",
        price_field="close",
        label=_RETURN_5D_T1_V1,
        allowed_workflows=("research", "backtest", "diagnostics"),
    ),
}


def describe_research_profile(profile_id: str) -> ResearchProfile:
    try:
        return RESEARCH_PROFILES[profile_id]
    except KeyError as exc:
        raise ValueError(f"unknown research profile: {profile_id}") from exc


def require_research_profile(profile_id: str) -> ResearchProfile:
    profile = describe_research_profile(profile_id)
    if not profile.implemented:
        raise ValueError(f"research profile is declared but not implemented: {profile_id}")
    return profile


def research_profile_from_settings(settings: Settings) -> ResearchProfile:
    experiment = settings.data.get("experiment", {})
    experiment = experiment if isinstance(experiment, Mapping) else {}
    configured = str(experiment.get("research_profile") or DEFAULT_RESEARCH_PROFILE_ID)
    profile = require_research_profile(configured)
    assert_settings_profile_compatible(settings, profile)
    return profile


def assert_settings_profile_compatible(settings: Settings, profile: ResearchProfile) -> None:
    experiment = settings.data.get("experiment", {})
    experiment = experiment if isinstance(experiment, Mapping) else {}
    label = experiment.get("label", {})
    label = label if isinstance(label, Mapping) else {}
    configured_label = str(label.get("spec") or "").strip()
    if configured_label and configured_label != profile.label.label_id:
        raise ValueError(
            f"research profile {profile.profile_id} requires label {profile.label.label_id!r}, "
            f"got {configured_label!r}"
        )

    qlib = settings.data.get("qlib", {})
    qlib = qlib if isinstance(qlib, Mapping) else {}
    configured_frequency = str(qlib.get("frequency") or "").strip()
    if configured_frequency and configured_frequency != profile.frequency:
        raise ValueError(
            f"research profile {profile.profile_id} requires frequency {profile.frequency!r}, "
            f"got {configured_frequency!r}"
        )

    research = settings.data.get("research", {})
    research = research if isinstance(research, Mapping) else {}
    configured_horizon = research.get("label_horizon_days")
    if configured_horizon is not None and int(configured_horizon) != profile.label.horizon_sessions:
        raise ValueError(
            f"research profile {profile.profile_id} requires label horizon "
            f"{profile.label.horizon_sessions}, got {configured_horizon}"
        )


def legacy_ashare_instrument(symbol: str) -> InstrumentSpec:
    text = symbol.strip().upper()
    if "." in text:
        qlib_symbol = ts_to_qlib(text)
        tushare_symbol = text
    else:
        tushare_symbol = qlib_to_ts(text)
        qlib_symbol = text
    exchange = qlib_symbol[:2]
    mic = _ASHARE_MIC[exchange]
    code = qlib_symbol[2:]
    return InstrumentSpec(
        instrument_id=f"CN.{mic}.EQUITY.{code}",
        asset_class="equity",
        subtype="common_stock",
        market="CN",
        venue=mic,
        currency="CNY",
        calendar_id=_CN_STOCK_CALENDAR_V1.calendar_id,
        tradable=True,
        aliases=(
            SymbolAlias("qlib", qlib_symbol),
            SymbolAlias("tushare", tushare_symbol),
        ),
    )


def ashare_etf_instrument(symbol: str) -> InstrumentSpec:
    """Map Qlib/TuShare ETF aliases to one provider-neutral research identity."""

    text = symbol.strip().upper()
    if "." in text:
        qlib_symbol = ts_to_qlib(text)
        tushare_symbol = text
    else:
        tushare_symbol = qlib_to_ts(text)
        qlib_symbol = text
    exchange = qlib_symbol[:2]
    if exchange not in {"SH", "SZ"}:
        raise ValueError(f"A-share ETF venue is not supported: {symbol}")
    mic = _ASHARE_MIC[exchange]
    code = qlib_symbol[2:]
    return InstrumentSpec(
        instrument_id=f"CN.{mic}.ETF.{code}",
        asset_class="equity",
        subtype="etf",
        market="CN",
        venue=mic,
        currency="CNY",
        calendar_id=_CN_STOCK_CALENDAR_V1.calendar_id,
        tradable=True,
        aliases=(
            SymbolAlias("qlib", qlib_symbol),
            SymbolAlias("tushare", tushare_symbol),
        ),
    )


def assert_information_available(*, available_at: datetime, as_of: datetime) -> None:
    if available_at.tzinfo is None or as_of.tzinfo is None:
        raise ValueError("available_at and as_of must be timezone-aware")
    if available_at > as_of:
        raise ValueError("future available_at cannot enter the current research sample")
