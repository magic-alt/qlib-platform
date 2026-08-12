from __future__ import annotations

from typing import Literal

import pandas as pd

from .daily_signal_runner import run_daily_signal
from .pretrade_runner import run_pretrade_actions
from .settings import Settings


def _require_open_trade_date(settings: Settings, business_date: str) -> None:
    path = settings.paths.metadata / "trade_calendar.parquet"
    if not path.is_file():
        raise FileNotFoundError(f"official trade calendar is missing: {path}")
    calendar = pd.read_parquet(path)
    if not {"cal_date", "is_open"}.issubset(calendar.columns):
        raise ValueError("official trade calendar is malformed")
    dates = pd.to_datetime(calendar["cal_date"], errors="coerce").dt.normalize()
    selected = calendar.loc[dates == pd.Timestamp(business_date).normalize(), "is_open"]
    if len(selected) != 1 or int(selected.iloc[0]) != 1:
        raise ValueError(f"business date is not an open trading day: {business_date}")


def run_production_day(
    settings: Settings,
    *,
    phase: Literal["close", "pretrade"],
    business_date: str,
    notify: bool = True,
    skip_sync: bool = False,
) -> object:
    _require_open_trade_date(settings, business_date)
    if phase == "close":
        return run_daily_signal(
            settings,
            as_of=business_date,
            notify=notify,
            skip_sync=skip_sync,
        )
    if phase == "pretrade":
        return run_pretrade_actions(settings, trade_date=business_date, notify=notify)
    raise ValueError(f"unsupported production phase: {phase}")
