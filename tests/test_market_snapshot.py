from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd
import pytest

from tushare_qlib.broker import ReadOnlyJsonClient
from tushare_qlib.freshness import SnapshotFreshnessError
from tushare_qlib.market_snapshot import HttpMarketSnapshotProvider, validate_market_snapshot


def _quotes(captured: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "instrument": "SH600000",
                "price": 10.0,
                "paused": 1,
                "is_limit_up": 0,
                "is_limit_down": 1,
                "adv20_volume": 1_000_000,
                "adv20_amount": 10_000_000,
                "as_of_trade_date": "2026-08-11",
                "snapshot_at_utc": captured,
            }
        ]
    )


def test_market_snapshot_preserves_status_limits_and_adv():
    now = datetime.now(timezone.utc).isoformat()

    def transport(request, timeout):
        return json.dumps(_quotes(now).to_dict("records")).encode()

    provider = HttpMarketSnapshotProvider(
        ReadOnlyJsonClient("https://market.invalid", transport=transport), max_age_seconds=120
    )

    result = provider.snapshot("2026-08-11", ["SH600000"])

    assert result.loc[0, ["paused", "is_limit_up", "is_limit_down"]].tolist() == [1, 0, 1]
    assert result.loc[0, "adv20_volume"] == 1_000_000
    assert result.loc[0, "adv20_amount"] == 10_000_000


def test_market_snapshot_rejects_stale_and_partial_quotes():
    stale = _quotes("2026-08-10T00:00:00Z")
    with pytest.raises(SnapshotFreshnessError):
        validate_market_snapshot(
            stale,
            trade_date="2026-08-11",
            instruments=["SH600000"],
            max_age_seconds=120,
        )
    with pytest.raises(ValueError, match="partial"):
        validate_market_snapshot(
            _quotes(datetime.now(timezone.utc).isoformat()),
            trade_date="2026-08-11",
            instruments=["SH600000", "SZ000001"],
            max_age_seconds=120,
        )


def test_market_snapshot_rejects_invalid_flags_and_adv():
    quotes = _quotes(datetime.now(timezone.utc).isoformat())
    quotes.loc[0, "paused"] = 2
    quotes.loc[0, "adv20_amount"] = -1
    with pytest.raises(ValueError):
        validate_market_snapshot(
            quotes,
            trade_date="2026-08-11",
            instruments=["SH600000"],
            max_age_seconds=120,
        )
