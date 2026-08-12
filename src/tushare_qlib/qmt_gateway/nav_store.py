from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class NavStore:
    """Durable prior-NAV and external-cash-flow state for daily PnL."""

    def __init__(self, state_dir: str | Path) -> None:
        directory = Path(state_dir)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "nav.sqlite3"
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS daily_nav (
                    trade_date TEXT PRIMARY KEY,
                    closing_nav REAL NOT NULL CHECK(closing_nav > 0),
                    captured_at_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cash_flow (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_date TEXT NOT NULL,
                    amount REAL NOT NULL CHECK(amount != 0),
                    reference TEXT NOT NULL,
                    recorded_at_utc TEXT NOT NULL
                );
                """
            )

    def _connection(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    @staticmethod
    def _now_text() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def capture(self, trade_date: str, closing_nav: float) -> None:
        value = float(closing_nav)
        if value <= 0:
            raise ValueError("closing NAV must be positive")
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO daily_nav(trade_date, closing_nav, captured_at_utc)
                VALUES (?, ?, ?)
                ON CONFLICT(trade_date) DO UPDATE SET
                  closing_nav=excluded.closing_nav,
                  captured_at_utc=excluded.captured_at_utc
                """,
                (trade_date, value, self._now_text()),
            )

    def record_cash_flow(self, trade_date: str, amount: float, reference: str = "") -> None:
        value = float(amount)
        if value == 0:
            raise ValueError("cash-flow amount must be non-zero")
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO cash_flow(trade_date, amount, reference, recorded_at_utc) VALUES (?, ?, ?, ?)",
                (trade_date, value, reference.strip(), self._now_text()),
            )

    def daily_pnl_pct(self, trade_date: str, total_asset: float) -> float:
        current = float(total_asset)
        with self._connection() as connection:
            prior = connection.execute(
                """
                SELECT closing_nav FROM daily_nav
                WHERE trade_date < ? ORDER BY trade_date DESC LIMIT 1
                """,
                (trade_date,),
            ).fetchone()
            flow = connection.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM cash_flow WHERE trade_date = ?",
                (trade_date,),
            ).fetchone()
        if prior is None:
            raise RuntimeError("no prior closing NAV is available")
        opening_nav = float(prior[0])
        if opening_nav <= 0:
            raise RuntimeError("prior closing NAV is invalid")
        return (current - opening_nav - float(flow[0])) / opening_nav
