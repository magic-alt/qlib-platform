from __future__ import annotations

import pandas as pd


class AShareMarketRules:
    def __init__(
        self,
        *,
        buy_lot_size: int = 100,
        max_participation_rate: float = 0.05,
        commission_bps: float = 3.0,
        min_commission: float = 5.0,
        sell_stamp_tax_bps: float = 5.0,
        transfer_fee_bps: float = 0.1,
        default_spread_bps: float = 4.0,
        slippage_bps: float = 2.0,
        impact_bps_at_full_participation: float = 50.0,
        main_board_limit_pct: float = 0.10,
        st_main_board_limit_pct: float = 0.05,
        growth_board_limit_pct: float = 0.20,
        beijing_limit_pct: float = 0.30,
        ipo_no_limit_days: int = 5,
        deal_price_column: str = "open",
    ) -> None:
        self.buy_lot_size = buy_lot_size
        self.max_participation_rate = max_participation_rate
        self.commission_bps = commission_bps
        self.min_commission = min_commission
        self.sell_stamp_tax_bps = sell_stamp_tax_bps
        self.transfer_fee_bps = transfer_fee_bps
        self.default_spread_bps = default_spread_bps
        self.slippage_bps = slippage_bps
        self.impact_bps_at_full_participation = impact_bps_at_full_participation
        self.main_board_limit_pct = main_board_limit_pct
        self.st_main_board_limit_pct = st_main_board_limit_pct
        self.growth_board_limit_pct = growth_board_limit_pct
        self.beijing_limit_pct = beijing_limit_pct
        self.ipo_no_limit_days = ipo_no_limit_days
        self.deal_price_column = deal_price_column
        self.validate()

    def validate(self) -> None:
        if self.buy_lot_size <= 0:
            raise ValueError("buy_lot_size must be positive")
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


def infer_price_limit_pct(
    *,
    board: str | None,
    is_st: bool,
    listing_days: int | None,
    rules: AShareMarketRules,
) -> float | None:
    if listing_days is not None and 0 <= listing_days < rules.ipo_no_limit_days:
        return None
    normalized = str(board or "MAIN").strip().upper().replace("-", "_")
    if normalized in {"STAR", "STAR_MARKET", "KCB", "科创板", "CHINEXT", "GEM", "创业板"}:
        return rules.growth_board_limit_pct
    if normalized in {"BSE", "BEIJING", "北交所"}:
        return rules.beijing_limit_pct
    if is_st:
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
