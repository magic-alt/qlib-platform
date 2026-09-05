from __future__ import annotations

import math
import re

import pandas as pd


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
        self.validate()

    def validate(self) -> None:
        if self.buy_lot_size <= 0:
            raise ValueError("buy_lot_size must be positive")
        if self.star_min_buy_size <= 0 or self.star_buy_increment <= 0:
            raise ValueError("STAR Market buy-size settings must be positive")
        if not 0 < self.max_participation_rate <= 1:
            raise ValueError("max_participation_rate must be in (0, 1]")
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


def is_star_market(instrument: str) -> bool:
    """Return whether an instrument identifier belongs to the SSE STAR Market.

    Both Qlib-style ``SH688981`` identifiers and vendor-style ``688981.SH``
    identifiers are accepted. The helper intentionally stays narrow instead
    of guessing a board from arbitrary metadata.
    """

    code = str(instrument).upper().strip()
    return code.startswith(("SH688", "SH689")) or bool(re.fullmatch(r"68[89]\d{3}\.SH", code))


def normalize_buy_quantity(instrument: str, quantity: float, rules: AShareMarketRules) -> int:
    """Round a proposed raw-share buy quantity down to a legal A-share quantity.

    Ordinary Shanghai/Shenzhen shares and ChiNext use 100-share buy lots.
    STAR Market buys require at least 200 shares and then allow one-share
    increments. Returning zero means the proposed order is below the minimum
    legal buy quantity after rounding.
    """

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
    if listing_days is not None and 0 <= listing_days < rules.ipo_no_limit_days:
        return None
    normalized = str(board or "MAIN").strip().upper().replace("-", "_")
    if normalized in {"STAR", "STAR_MARKET", "KCB", "科创板", "CHINEXT", "GEM", "创业板"}:
        return rules.growth_board_limit_pct
    if normalized in {"BSE", "BEIJING", "北交所"}:
        return rules.beijing_limit_pct
    if is_st:
        if trade_date is not None:
            resolved_date = pd.Timestamp(trade_date).normalize()
            if resolved_date < rules.st_main_board_reform_date:
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


def resolve_limits(row: pd.Series, rules: AShareMarketRules) -> tuple[float | None, float | None]:
    explicit_up = pd.to_numeric(pd.Series([row.get("limit_up")]), errors="coerce").iloc[0]
    explicit_down = pd.to_numeric(pd.Series([row.get("limit_down")]), errors="coerce").iloc[0]
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
    if frame[[rules.deal_price_column, "close", "volume"]].isna().any().any():
        raise ValueError("market data contains invalid price/volume values")
    if (frame[[rules.deal_price_column, "close"]] <= 0).any().any() or (frame["volume"] < 0).any():
        raise ValueError("market prices must be positive and volume non-negative")
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
    frame["quantity"] = pd.to_numeric(frame["quantity"], errors="coerce")
    if frame["trade_date"].isna().any() or not frame["side"].isin({"BUY", "SELL"}).all():
        raise ValueError("orders contain invalid date or side")
    if frame["quantity"].isna().any() or (frame["quantity"] <= 0).any():
        raise ValueError("order quantity must be positive")
    frame["quantity"] = frame["quantity"].astype(int)
    if "order_id" not in frame:
        frame["order_id"] = [f"order_{index:06d}" for index in range(len(frame))]
    else:
        frame["order_id"] = frame["order_id"].astype(str)
    return frame.sort_values(["trade_date", "order_id"]).reset_index(drop=True)
