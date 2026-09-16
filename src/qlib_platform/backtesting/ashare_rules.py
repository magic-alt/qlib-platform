from __future__ import annotations

import math
import re

import pandas as pd

from qlib_platform.backtesting.market_rule_set import (
    FeeRegime,
    MarketRuleSet,
    production_realism_rule_set,
)


class AShareMarketRules:
    def __init__(
        self,
        *,
        buy_lot_size: int = 100,
        star_min_buy_size: int = 200,
        star_buy_increment: int = 1,
        max_participation_rate: float = 0.05,
        commission_bps: float = 1.0,
        min_commission: float = 0.0,
        sell_stamp_tax_bps: float = 5.0,
        transfer_fee_bps: float = 0.1,
        default_spread_bps: float = 4.0,
        slippage_bps: float = 2.0,
        impact_bps_at_full_participation: float = 50.0,
        main_board_limit_pct: float = 0.10,
        st_main_board_limit_pct_legacy: float = 0.05,
        st_main_board_limit_pct: float = 0.10,
        st_main_board_reform_date: str = "2026-07-06",
        growth_board_limit_pct: float = 0.20,
        beijing_limit_pct: float = 0.30,
        ipo_no_limit_days: int = 5,
        deal_price_column: str = "open",
        price_basis: str = "raw_unadjusted",
        market_rule_set: MarketRuleSet | None = None,
    ) -> None:
        self.buy_lot_size = buy_lot_size
        self.star_min_buy_size = star_min_buy_size
        self.star_buy_increment = star_buy_increment
        self.max_participation_rate = max_participation_rate
        self.commission_bps = commission_bps
        self.min_commission = min_commission
        self.sell_stamp_tax_bps = sell_stamp_tax_bps
        self.transfer_fee_bps = transfer_fee_bps
        self.default_spread_bps = default_spread_bps
        self.slippage_bps = slippage_bps
        self.impact_bps_at_full_participation = impact_bps_at_full_participation
        self.main_board_limit_pct = main_board_limit_pct
        self.st_main_board_limit_pct_legacy = st_main_board_limit_pct_legacy
        self.st_main_board_limit_pct = st_main_board_limit_pct
        self.st_main_board_reform_date = pd.Timestamp(st_main_board_reform_date).normalize()
        self.growth_board_limit_pct = growth_board_limit_pct
        self.beijing_limit_pct = beijing_limit_pct
        self.ipo_no_limit_days = ipo_no_limit_days
        self.deal_price_column = deal_price_column
        self.price_basis = str(price_basis).strip().lower()
        self.market_rule_set = market_rule_set or production_realism_rule_set()
        self.validate()

    @property
    def market_rule_set_id(self) -> str:
        return self.market_rule_set.market_rule_set_id

    @property
    def cost_model_id(self) -> str:
        return self.market_rule_set.cost_model_id

    @property
    def fill_model_id(self) -> str:
        return self.market_rule_set.fill_model_id

    def validate(self) -> None:
        if self.buy_lot_size <= 0:
            raise ValueError("buy_lot_size must be positive")
        if self.star_min_buy_size <= 0 or self.star_buy_increment <= 0:
            raise ValueError("STAR Market buy-size settings must be positive")
        if not 0 < self.max_participation_rate <= 1:
            raise ValueError("max_participation_rate must be in (0, 1]")
        if self.price_basis not in {"raw_unadjusted", "adjusted"}:
            raise ValueError("price_basis must be raw_unadjusted or adjusted")
        costs = (
            self.commission_bps,
            self.min_commission,
            self.sell_stamp_tax_bps,
            self.transfer_fee_bps,
            self.default_spread_bps,
            self.slippage_bps,
            self.impact_bps_at_full_participation,
        )
        if any(value < 0 for value in costs):
            raise ValueError("cost assumptions must be non-negative")
        price_limits = (
            self.main_board_limit_pct,
            self.st_main_board_limit_pct_legacy,
            self.st_main_board_limit_pct,
            self.growth_board_limit_pct,
            self.beijing_limit_pct,
        )
        if any(not 0 < value < 1 for value in price_limits):
            raise ValueError("price-limit assumptions must be in (0, 1)")

    def fee_regime_for(self, trade_date: object, venue: str = "SSE_SZSE") -> FeeRegime:
        return self.market_rule_set.fee_regime_for(trade_date, venue)

    def identity_manifest(self) -> dict[str, object]:
        return {
            **self.market_rule_set.to_manifest(),
            "marketRuleSetSha256": self.market_rule_set.fingerprint,
            "commissionBps": self.commission_bps,
            "minCommission": self.min_commission,
            "maxParticipationRate": self.max_participation_rate,
            "dealPrice": self.deal_price_column,
            "priceBasis": self.price_basis,
        }


def is_star_market(instrument: str) -> bool:
    """Return whether an instrument identifier belongs to the SSE STAR Market."""

    code = str(instrument).upper().strip()
    return code.startswith(("SH688", "SH689")) or bool(re.fullmatch(r"68[89]\d{3}\.SH", code))


def _board_name(board: object | None) -> str:
    return str(board or "MAIN").strip().upper().replace("-", "_")


def _is_chinext(board: object | None) -> bool:
    return _board_name(board) in {"CHINEXT", "GEM", "创业板"}


def _is_star(board: object | None) -> bool:
    return _board_name(board) in {"STAR", "STAR_MARKET", "KCB", "科创板"}


def _is_beijing(board: object | None) -> bool:
    return _board_name(board) in {"BSE", "BEIJING", "北交所"}


def fee_venue(instrument: str, board: object | None = None) -> str:
    """Return the statutory fee venue for a normalized A-share identifier."""

    code = str(instrument).upper().strip()
    if _is_beijing(board) or code.endswith(".BJ") or code.startswith("BJ"):
        return "BSE"
    return "SSE_SZSE"


def normalize_buy_quantity(instrument: str, quantity: float, rules: AShareMarketRules) -> int:
    """Round a proposed raw-share buy quantity down to a legal A-share quantity."""

    if not math.isfinite(float(quantity)) or quantity <= 0:
        return 0
    raw = int(math.floor(float(quantity) + 1e-9))
    if is_star_market(instrument):
        if raw < rules.star_min_buy_size:
            return 0
        return (
            rules.star_min_buy_size
            + ((raw - rules.star_min_buy_size) // rules.star_buy_increment) * rules.star_buy_increment
        )
    return (raw // rules.buy_lot_size) * rules.buy_lot_size


def normalize_sell_quantity(
    instrument: str,
    requested: float,
    available: int,
    rules: AShareMarketRules,
) -> int:
    """Return a legal sell quantity without creating or destroying odd-lot inventory."""

    if not math.isfinite(float(requested)) or requested <= 0 or available <= 0:
        return 0
    raw = min(int(math.floor(float(requested) + 1e-9)), int(available))
    minimum = rules.star_min_buy_size if is_star_market(instrument) else rules.buy_lot_size
    if available < minimum:
        return int(available) if raw >= available else 0
    if is_star_market(instrument):
        return raw if raw >= minimum else 0
    return (raw // minimum) * minimum


def is_legal_buy_quantity(instrument: str, quantity: float, rules: AShareMarketRules) -> bool:
    if not math.isfinite(float(quantity)) or quantity <= 0:
        return False
    normalized = normalize_buy_quantity(instrument, quantity, rules)
    return normalized > 0 and math.isclose(float(quantity), float(normalized), rel_tol=0.0, abs_tol=1e-8)


def infer_price_limit_pct(
    *,
    board: str | None,
    is_st: bool,
    listing_days: int | None,
    rules: AShareMarketRules,
    trade_date: object | None = None,
) -> float | None:
    normalized = _board_name(board)
    resolved_date = pd.Timestamp(trade_date).normalize() if trade_date is not None else None

    if _is_star(normalized):
        if listing_days is not None and 0 <= listing_days < rules.ipo_no_limit_days:
            return None
        return rules.growth_board_limit_pct

    if _is_chinext(normalized):
        reform_date = pd.Timestamp("2020-08-24")
        if resolved_date is None or resolved_date >= reform_date:
            if listing_days is not None and 0 <= listing_days < rules.ipo_no_limit_days:
                return None
            return rules.growth_board_limit_pct
        return rules.st_main_board_limit_pct_legacy if is_st else rules.main_board_limit_pct

    if _is_beijing(normalized):
        if listing_days is not None and listing_days == 0:
            return None
        return rules.beijing_limit_pct

    registration_reform = pd.Timestamp("2023-04-10")
    if listing_days is not None and 0 <= listing_days < rules.ipo_no_limit_days:
        if resolved_date is None or resolved_date >= registration_reform:
            return None
        if listing_days == 0:
            raise ValueError(
                "legacy main-board IPO first-session limits are asymmetric; provide explicit limit_up/limit_down"
            )
    if is_st:
        if resolved_date is not None and resolved_date < rules.st_main_board_reform_date:
            return rules.st_main_board_limit_pct_legacy
        return rules.st_main_board_limit_pct
    return rules.main_board_limit_pct


def as_bool(row: pd.Series, column: str, default: bool = False) -> bool:
    value = row.get(column, default)
    if pd.isna(value):
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def trading_status(row: pd.Series) -> str:
    return str(row.get("trading_status") or "ACTIVE").strip().upper().replace("-", "_")


def row_is_suspended(row: pd.Series) -> bool:
    return as_bool(row, "paused") or trading_status(row) in {"SUSPENDED", "HALTED", "TEMP_HALTED"}


def lifecycle_rejection(row: pd.Series) -> str | None:
    status = trading_status(row)
    if as_bool(row, "listed", True) is False or status in {"PRE_LISTING", "NOT_LISTED"}:
        return "not_listed"
    if as_bool(row, "delisted") or status in {"DELISTED", "POST_DELISTING"}:
        return "delisted"
    return None


def resolve_limits(row: pd.Series, rules: AShareMarketRules) -> tuple[float | None, float | None]:
    if as_bool(row, "no_price_limit"):
        return None, None
    explicit_up = pd.to_numeric(pd.Series([row.get("limit_up")]), errors="coerce").iloc[0]
    explicit_down = pd.to_numeric(pd.Series([row.get("limit_down")]), errors="coerce").iloc[0]
    if pd.notna(explicit_up) != pd.notna(explicit_down):
        raise ValueError("limit_up and limit_down must be supplied together")
    if pd.notna(explicit_up) and pd.notna(explicit_down):
        return float(explicit_up), float(explicit_down)
    previous_close = pd.to_numeric(pd.Series([row.get("prev_close")]), errors="coerce").iloc[0]
    if pd.isna(previous_close) or float(previous_close) <= 0:
        return None, None
    configured = pd.to_numeric(pd.Series([row.get("price_limit_pct")]), errors="coerce").iloc[0]
    if pd.notna(configured):
        pct: float | None = float(configured)
    else:
        listing_value = pd.to_numeric(pd.Series([row.get("listing_days")]), errors="coerce").iloc[0]
        pct = infer_price_limit_pct(
            board=str(row.get("board", "MAIN")),
            is_st=as_bool(row, "is_st"),
            listing_days=int(listing_value) if pd.notna(listing_value) else None,
            rules=rules,
            trade_date=row.get("trade_date"),
        )
    if pct is None:
        return None, None
    return float(previous_close) * (1.0 + pct), float(previous_close) * (1.0 - pct)


def normalize_market_data(bars: pd.DataFrame, rules: AShareMarketRules) -> pd.DataFrame:
    required = {"trade_date", "instrument", rules.deal_price_column, "close", "volume"}
    missing = sorted(required - set(bars.columns))
    if missing:
        raise ValueError(f"market data missing required columns: {missing}")
    frame = bars.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.normalize()
    frame["instrument"] = frame["instrument"].astype(str).str.upper().str.strip()
    if frame["trade_date"].isna().any() or frame.duplicated(["trade_date", "instrument"]).any():
        raise ValueError("market data has invalid dates or duplicate rows")
    for column in (rules.deal_price_column, "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame[["close", "volume"]].isna().any().any():
        raise ValueError("market data contains invalid close/volume values")
    if (frame["close"] <= 0).any() or (frame["volume"] < 0).any():
        raise ValueError("market close must be positive and volume non-negative")
    paused = frame.apply(row_is_suspended, axis=1)
    invalid_deal = frame[rules.deal_price_column].isna() | (frame[rules.deal_price_column] <= 0)
    if (invalid_deal & ~paused).any():
        raise ValueError("tradable market rows require a positive deal price")
    return frame.sort_values(["trade_date", "instrument"]).reset_index(drop=True)


def normalize_orders(orders: pd.DataFrame) -> pd.DataFrame:
    required = {"trade_date", "instrument", "side", "quantity"}
    missing = sorted(required - set(orders.columns))
    if missing:
        raise ValueError(f"orders missing required columns: {missing}")
    frame = orders.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.normalize()
    frame["instrument"] = frame["instrument"].astype(str).str.upper().str.strip()
    frame["side"] = frame["side"].astype(str).str.upper().str.strip()
    quantity = pd.to_numeric(frame["quantity"], errors="coerce")
    if frame["trade_date"].isna().any() or not frame["side"].isin({"BUY", "SELL"}).all():
        raise ValueError("orders contain invalid date or side")
    if quantity.isna().any() or (quantity <= 0).any():
        raise ValueError("order quantity must be positive")
    rounded = quantity.round()
    if ((quantity - rounded).abs() > 1e-9).any():
        raise ValueError("order quantity must be an integer number of raw shares")
    frame["quantity"] = rounded.astype(int)
    if "order_id" not in frame:
        frame["order_id"] = [f"order_{index:06d}" for index in range(len(frame))]
    else:
        frame["order_id"] = frame["order_id"].astype(str)
    return frame.sort_values(["trade_date", "order_id"]).reset_index(drop=True)
