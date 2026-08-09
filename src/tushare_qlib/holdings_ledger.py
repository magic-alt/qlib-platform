from __future__ import annotations

import os
from pathlib import Path

import pandas as pd


_LEDGER_COLUMNS = ["instrument", "opened_trade_date", "last_quantity", "as_of_date"]


def _normalise_positions(positions: pd.DataFrame) -> pd.DataFrame:
    required = {"instrument", "quantity", "available_quantity"}
    missing = required - set(positions.columns)
    if missing:
        raise ValueError(f"positions missing columns: {sorted(missing)}")
    frame = positions.copy()
    frame["instrument"] = frame["instrument"].astype(str).str.upper().str.strip()
    for column in ("quantity", "available_quantity"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    if frame["instrument"].duplicated().any():
        raise ValueError("positions contains duplicate instruments")
    return frame.loc[frame["quantity"] > 0, ["instrument", "quantity", "available_quantity"]].reset_index(drop=True)


def _normalise_fills(fills: pd.DataFrame | None) -> pd.DataFrame:
    columns = ["fill_id", "trade_date", "instrument", "side", "quantity", "fill_price"]
    if fills is None or fills.empty:
        return pd.DataFrame(columns=columns)
    missing = set(columns) - set(fills.columns)
    if missing:
        raise ValueError(f"fills missing columns: {sorted(missing)}")
    frame = fills[columns].copy()
    frame["fill_id"] = frame["fill_id"].astype(str).str.strip()
    if frame["fill_id"].eq("").any() or frame["fill_id"].duplicated().any():
        raise ValueError("fills must have unique, non-empty fill_id values")
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise").dt.normalize()
    frame["instrument"] = frame["instrument"].astype(str).str.upper().str.strip()
    frame["side"] = frame["side"].astype(str).str.upper().str.strip()
    if not frame["side"].isin({"BUY", "SELL"}).all():
        raise ValueError("fill side must be BUY or SELL")
    frame["quantity"] = pd.to_numeric(frame["quantity"], errors="raise")
    frame["fill_price"] = pd.to_numeric(frame["fill_price"], errors="raise")
    if (frame["quantity"] <= 0).any() or (frame["fill_price"] <= 0).any():
        raise ValueError("fill quantity and fill_price must be positive")
    return frame


def _read_ledger(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=_LEDGER_COLUMNS)
    frame = pd.read_parquet(path)
    missing = set(_LEDGER_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"holdings ledger missing columns: {sorted(missing)}")
    frame = frame[_LEDGER_COLUMNS].copy()
    frame["instrument"] = frame["instrument"].astype(str).str.upper().str.strip()
    frame["opened_trade_date"] = pd.to_datetime(frame["opened_trade_date"], errors="raise").dt.normalize()
    frame["as_of_date"] = pd.to_datetime(frame["as_of_date"], errors="raise").dt.normalize()
    frame["last_quantity"] = pd.to_numeric(frame["last_quantity"], errors="raise")
    if frame["instrument"].duplicated().any():
        raise ValueError("holdings ledger contains duplicate active instruments")
    return frame


def _normalise_initial_holdings(initial_holdings: pd.DataFrame | None) -> pd.DataFrame:
    if initial_holdings is None or initial_holdings.empty:
        return pd.DataFrame(columns=["instrument", "opened_trade_date"])
    required = {"instrument", "opened_trade_date"}
    missing = required - set(initial_holdings.columns)
    if missing:
        raise ValueError(f"initial_holdings missing columns: {sorted(missing)}")
    frame = initial_holdings[list(required)].copy()
    frame["instrument"] = frame["instrument"].astype(str).str.upper().str.strip()
    frame["opened_trade_date"] = pd.to_datetime(frame["opened_trade_date"], errors="raise").dt.normalize()
    if frame["instrument"].duplicated().any():
        raise ValueError("initial_holdings contains duplicate instruments")
    return frame


def _calendar(calendar_path: str | Path) -> pd.DatetimeIndex:
    frame = pd.read_parquet(calendar_path)
    required = {"cal_date", "is_open"}
    if not required.issubset(frame.columns):
        raise ValueError("trading calendar must contain cal_date and is_open")
    return pd.DatetimeIndex(
        pd.to_datetime(frame.loc[pd.to_numeric(frame["is_open"], errors="coerce") == 1, "cal_date"], errors="raise")
        .dt.normalize()
        .sort_values()
        .unique()
    )


def _holding_days(calendar: pd.DatetimeIndex, opened: pd.Timestamp, as_of: pd.Timestamp) -> int:
    return int(((calendar >= opened) & (calendar <= as_of)).sum())


def _atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(temporary, index=False)
    os.replace(temporary, path)


def reconcile_holdings(
    positions: pd.DataFrame,
    fills: pd.DataFrame | None,
    *,
    as_of_date: str | pd.Timestamp,
    calendar_path: str | Path,
    ledger_path: str | Path,
    initial_holdings: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Reconcile broker truth to a persistent active-position holding-period ledger.

    A position appearing without an earlier ledger entry must be justified by a
    fill since the last reconciliation or by an explicit initial-holdings file.
    This deliberately fails closed rather than incorrectly allowing a sell.
    """

    as_of = pd.Timestamp(as_of_date).normalize()
    broker = _normalise_positions(positions)
    fill_frame = _normalise_fills(fills)
    ledger_file = Path(ledger_path).expanduser().resolve()
    ledger = _read_ledger(ledger_file)
    seeds = _normalise_initial_holdings(initial_holdings)
    calendar = _calendar(calendar_path)
    if as_of not in calendar:
        raise ValueError(f"as_of_date is not an open trading day: {as_of.date()}")
    if not ledger.empty and ledger["as_of_date"].max() > as_of:
        raise ValueError("ledger is newer than the requested reconciliation date")

    previous_as_of = ledger["as_of_date"].max() if not ledger.empty else None
    prior = ledger.set_index("instrument") if not ledger.empty else pd.DataFrame(index=pd.Index([], name="instrument"))
    seed_dates = seeds.set_index("instrument")["opened_trade_date"] if not seeds.empty else pd.Series(dtype="datetime64[ns]")
    rows: list[dict[str, object]] = []
    broker_instruments = set(broker["instrument"])

    for row in broker.itertuples(index=False):
        instrument = str(row.instrument)
        if instrument in prior.index and float(prior.at[instrument, "last_quantity"]) > 0:
            opened = pd.Timestamp(prior.at[instrument, "opened_trade_date"]).normalize()
        elif instrument in seed_dates.index:
            opened = pd.Timestamp(seed_dates.at[instrument]).normalize()
        else:
            candidate_fills = fill_frame.loc[
                (fill_frame["instrument"] == instrument)
                & (fill_frame["side"] == "BUY")
                & (fill_frame["trade_date"] <= as_of)
            ]
            if previous_as_of is not None:
                candidate_fills = candidate_fills.loc[candidate_fills["trade_date"] > previous_as_of]
            if candidate_fills.empty:
                raise ValueError(
                    f"unexplained broker holding {instrument}; supply a BUY fill or initial_holdings with opened_trade_date"
                )
            opened = pd.Timestamp(candidate_fills["trade_date"].min()).normalize()
        if opened > as_of:
            raise ValueError(f"opened_trade_date after as_of_date for {instrument}")
        rows.append(
            {
                "instrument": instrument,
                "opened_trade_date": opened,
                "last_quantity": float(row.quantity),
                "as_of_date": as_of,
                "available_quantity": float(row.available_quantity),
                "holding_days": _holding_days(calendar, opened, as_of),
            }
        )

    state = pd.DataFrame(
        rows,
        columns=["instrument", "opened_trade_date", "last_quantity", "as_of_date", "available_quantity", "holding_days"],
    )
    closed_rows = [
        {
            "instrument": instrument,
            "opened_trade_date": prior.at[instrument, "opened_trade_date"],
            "last_quantity": 0.0,
            "as_of_date": as_of,
        }
        for instrument in prior.index
        if instrument not in broker_instruments
    ]
    persisted = state[_LEDGER_COLUMNS].copy()
    if closed_rows:
        closed = pd.DataFrame(closed_rows, columns=_LEDGER_COLUMNS)
        persisted = closed if persisted.empty else pd.concat([persisted, closed], ignore_index=True)
    _atomic_parquet(persisted, ledger_file)
    return state.sort_values("instrument").reset_index(drop=True)
