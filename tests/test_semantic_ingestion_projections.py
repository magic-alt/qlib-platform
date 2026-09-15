from __future__ import annotations

import pandas as pd
import pytest

from qlib_platform.data.semantic_ingestion import legacy_projection
from qlib_platform.data.sources.semantic import CanonicalBatch, DataSourceContractError


def _batch(kind: str, frame: pd.DataFrame) -> CanonicalBatch:
    return CanonicalBatch(kind, frame, "1.0", "Asia/Shanghai")


def test_daily_basic_projection_reverses_canonical_unit_conversions() -> None:
    frame = pd.DataFrame(
        {
            "instrument": ["SH600000"],
            "trading_date": ["2026-09-10"],
            "event_time": ["2026-09-10T15:00:00+08:00"],
            "close": [10.0],
            "turnover_rate": [0.025],
            "turnover_rate_f": [0.03],
            "dv_ratio": [0.012],
            "dv_ttm": [0.01],
            "volume_ratio": [1.5],
            "pe": [8.0],
            "pe_ttm": [9.0],
            "pb": [1.0],
            "ps": [2.0],
            "ps_ttm": [2.1],
            "total_shares": [1_000_000.0],
            "float_shares": [800_000.0],
            "free_shares": [600_000.0],
            "total_market_value": [2_500_000.0],
            "circulating_market_value": [2_000_000.0],
            "limit_status": [0.0],
        }
    )

    projected = legacy_projection(_batch("equity_daily_basic", frame))

    assert projected.loc[0, "ts_code"] == "600000.SH"
    assert projected.loc[0, "turnover_rate"] == pytest.approx(2.5)
    assert projected.loc[0, "total_share"] == pytest.approx(100.0)
    assert projected.loc[0, "total_mv"] == pytest.approx(250.0)
    assert projected.loc[0, "limit_status"] == 0.0


def test_adjustment_master_and_calendar_projections_keep_legacy_shapes() -> None:
    adjustment = legacy_projection(
        _batch(
            "adjustment_factor",
            pd.DataFrame(
                {
                    "instrument": ["SZ000001"],
                    "trading_date": ["2026-09-10"],
                    "event_time": ["2026-09-10T15:00:00+08:00"],
                    "adjustment_factor": [1.25],
                }
            ),
        )
    )
    master = legacy_projection(
        _batch(
            "instrument_master",
            pd.DataFrame(
                {
                    "instrument": ["SH600000"],
                    "symbol": ["600000"],
                    "name": ["Pudong"],
                    "area": ["Shanghai"],
                    "industry": ["Bank"],
                    "market": ["Main Board"],
                    "exchange": ["SSE"],
                    "listing_status": ["L"],
                    "list_date": ["19991110"],
                    "delist_date": [None],
                    "is_hs": ["N"],
                    "act_name": [None],
                    "act_ent_type": [None],
                }
            ),
        )
    )
    calendar = legacy_projection(
        _batch(
            "trading_calendar",
            pd.DataFrame(
                {
                    "trading_date": ["2026-09-10"],
                    "exchange": ["SSE"],
                    "is_open": [True],
                    "previous_trading_date": ["2026-09-09"],
                }
            ),
        )
    )

    assert adjustment.to_dict("records") == [
        {"ts_code": "000001.SZ", "trade_date": "20260910", "adj_factor": 1.25}
    ]
    assert master.loc[0, "ts_code"] == "600000.SH"
    assert master.loc[0, "list_status"] == "L"
    assert calendar.to_dict("records") == [
        {"exchange": "SSE", "cal_date": "20260910", "is_open": 1, "pretrade_date": "20260909"}
    ]


def test_projection_fails_closed_for_unknown_or_incomplete_canonical_schema() -> None:
    with pytest.raises(DataSourceContractError, match="no legacy compatibility projection"):
        legacy_projection(_batch("unknown", pd.DataFrame()))

    with pytest.raises(DataSourceContractError, match="daily projection missing"):
        legacy_projection(
            _batch(
                "equity_daily",
                pd.DataFrame(
                    {
                        "instrument": ["SH600000"],
                        "trading_date": ["2026-09-10"],
                        "open": [10.0],
                    }
                ),
            )
        )

    with pytest.raises(DataSourceContractError, match="adjustment-factor projection"):
        legacy_projection(
            _batch(
                "adjustment_factor",
                pd.DataFrame({"instrument": ["SH600000"], "trading_date": ["2026-09-10"]}),
            )
        )
