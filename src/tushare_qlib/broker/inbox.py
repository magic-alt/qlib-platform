from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .base import BrokerSnapshot, validate_broker_snapshot


class InboxBrokerAdapter:
    """Controlled file fallback for drills and disconnected operation."""

    source_name = "inbox"

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def snapshot(self, trade_date: str) -> BrokerSnapshot:
        root = self.root / trade_date
        required = [root / "positions.csv", root / "account.json"]
        missing = [path.name for path in required if not path.is_file()]
        if missing:
            raise FileNotFoundError(f"pretrade broker inbox is incomplete for {trade_date}: {missing}")
        snapshot = BrokerSnapshot(
            account=json.loads((root / "account.json").read_text(encoding="utf-8")),
            positions=pd.read_csv(root / "positions.csv"),
            orders=pd.read_csv(root / "orders.csv") if (root / "orders.csv").is_file() else pd.DataFrame(),
            fills=pd.read_csv(root / "fills.csv") if (root / "fills.csv").is_file() else pd.DataFrame(),
            initial_holdings=(
                pd.read_csv(root / "initial_holdings.csv")
                if (root / "initial_holdings.csv").is_file()
                else None
            ),
        )
        return validate_broker_snapshot(snapshot, trade_date)
