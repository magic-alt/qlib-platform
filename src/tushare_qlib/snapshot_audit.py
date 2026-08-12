from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd


def write_snapshot_audit(
    output: Path,
    *,
    account: Mapping[str, Any],
    positions: pd.DataFrame,
    orders: pd.DataFrame,
    fills: pd.DataFrame,
    quotes: pd.DataFrame,
    broker_source: str,
    market_source: str,
) -> Path:
    root = output / "input_snapshot"
    root.mkdir(parents=True, exist_ok=True)
    account_fields = (
        "account_id",
        "as_of_trade_date",
        "snapshot_at_utc",
        "portfolio_value",
        "cash",
        "daily_pnl_pct",
    )
    account_audit = {key: account[key] for key in account_fields if key in account}
    (root / "account.json").write_text(
        json.dumps(account_audit, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    positions.to_parquet(root / "positions.parquet", index=False)
    orders.to_parquet(root / "broker_orders.parquet", index=False)
    fills.to_parquet(root / "broker_fills.parquet", index=False)
    quotes.to_parquet(root / "quotes.parquet", index=False)
    (root / "sources.json").write_text(
        json.dumps(
            {"broker": broker_source, "market": market_source}, ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    return root
