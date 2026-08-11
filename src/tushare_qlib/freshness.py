from __future__ import annotations

from datetime import datetime, timezone
import pandas as pd


class SnapshotFreshnessError(ValueError):
    """Raised when an execution snapshot is not an auditable current snapshot."""


def validate_execution_snapshot(
    frame: pd.DataFrame,
    *,
    name: str,
    trade_date: str,
    max_age_seconds: int,
    now_utc: datetime | None = None,
) -> pd.Timestamp:
    """Fail closed unless every row belongs to one fresh, declared snapshot.

    A business date alone cannot establish freshness: a snapshot must carry its
    UTC capture time and the trade date for which it was obtained.  Requiring
    one value per file prevents silently mixing broker/quote responses.
    """
    required = {"snapshot_at_utc", "as_of_trade_date"}
    missing = required - set(frame.columns)
    if missing:
        raise SnapshotFreshnessError(f"{name} missing freshness columns: {sorted(missing)}")
    if frame.empty:
        raise SnapshotFreshnessError(f"{name} snapshot is empty")
    as_of = pd.to_datetime(frame["as_of_trade_date"], errors="coerce").dt.normalize()
    expected = pd.Timestamp(trade_date).normalize()
    if as_of.isna().any() or as_of.nunique() != 1 or as_of.iloc[0] != expected:
        raise SnapshotFreshnessError(f"{name} as_of_trade_date must be exactly {expected.date()}")
    captured = pd.to_datetime(frame["snapshot_at_utc"], errors="coerce", utc=True)
    if captured.isna().any() or captured.nunique() != 1:
        raise SnapshotFreshnessError(f"{name} snapshot_at_utc must be one valid UTC instant")
    now = now_utc or datetime.now(timezone.utc)
    now_ts = pd.Timestamp(now)
    if now_ts.tzinfo is None:
        now_ts = now_ts.tz_localize("UTC")
    age = (now_ts - captured.iloc[0]).total_seconds()
    if age < -5 or age > max_age_seconds:
        raise SnapshotFreshnessError(
            f"{name} snapshot is stale or clock-skewed (age_seconds={age:.1f}, max={max_age_seconds})"
        )
    return captured.iloc[0]
