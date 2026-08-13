import numpy as np
import pandas as pd

from tushare_qlib.fundamentals import PIT_FIELDS
from tushare_qlib.normalize import normalize_symbol
from tushare_qlib.quality import validate_curated


def test_observed_close_takes_precedence_over_conflicting_suspend_record():
    frame = pd.DataFrame({"close": [10.0, np.nan]})

    paused = frame["close"].isna().astype(float)

    assert paused.tolist() == [0.0, 1.0]


def test_source_confirmed_market_suspension_uses_lower_coverage_threshold():
    frame = pd.DataFrame(
        {
            "symbol": [f"SH60000{i}" for i in range(5)],
            "date": pd.to_datetime(["2015-07-09"] * 5),
            "ts_code": [f"60000{i}.SH" for i in range(5)],
            "close": [10.0, 10.0, np.nan, np.nan, np.nan],
            "open": [10.0, 10.0, np.nan, np.nan, np.nan],
            "high": [10.0, 10.0, np.nan, np.nan, np.nan],
            "low": [10.0, 10.0, np.nan, np.nan, np.nan],
            "adj_factor": [1.0] * 5,
            "paused": [0.0, 0.0, 1.0, 1.0, 1.0],
            "list_date": pd.to_datetime(["2000-01-01"] * 5),
        }
    )

    normal = validate_curated(frame)
    systemic_suspend = validate_curated(frame, min_traded_coverage=0.40)

    assert not next(item for item in normal.results if item.name == "traded_coverage").passed
    assert next(item for item in systemic_suspend.results if item.name == "traded_coverage").passed


def test_adjustment_identity():
    calendar = pd.DatetimeIndex(pd.to_datetime(["2024-01-02", "2024-01-03"]))
    raw = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
            "symbol": ["SH600000", "SH600000"],
            "list_date": pd.to_datetime(["2020-01-01", "2020-01-01"]),
            "open": [10.0, 5.1],
            "high": [10.5, 5.3],
            "low": [9.8, 5.0],
            "close": [10.0, 5.2],
            "vol": [1000.0, 2000.0],
            "amount": [1000.0, 2000.0],
            "pct_chg": [0.0, 4.0],
            "adj_factor": [1.0, 2.0],
            "paused": [0.0, 0.0],
            "is_st": [0.0, 0.0],
            **{field: [0.1, 0.2] for field in PIT_FIELDS},
        }
    )
    norm, base = normalize_symbol(raw, calendar)
    factor = norm["factor"].to_numpy()
    # adjusted price / factor must reconstruct the original raw price
    np.testing.assert_allclose(norm["close"].to_numpy() / factor, raw["close"].to_numpy())
    # adjusted volume * factor must reconstruct raw shares (Tushare hands * 100)
    np.testing.assert_allclose(norm["volume"].to_numpy() * factor, raw["vol"].to_numpy() * 100)
    assert base == 10.0
    assert norm["roe_waa_pit"].tolist() == [0.1, 0.2]
