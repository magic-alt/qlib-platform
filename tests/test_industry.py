from __future__ import annotations

import pandas as pd
import pytest

from tushare_qlib.industry import build_sw2021_industry_intervals


def test_sw2021_membership_is_converted_to_non_overlapping_pit_intervals():
    members = pd.DataFrame(
        [
            {
                "l1_code": "801010.SI",
                "l1_name": "Agriculture",
                "ts_code": "600000.SH",
                "in_date": "20200101",
                "out_date": "20211231",
            },
            {
                "l1_code": "801020.SI",
                "l1_name": "Mining",
                "ts_code": "600000.SH",
                "in_date": "20220101",
                "out_date": None,
            },
        ]
    )

    result = build_sw2021_industry_intervals(members, coverage_end="2026-08-24")

    assert result["instrument"].tolist() == ["SH600000", "SH600000"]
    assert result["industry_code"].tolist() == ["801010", "801020"]
    assert result.iloc[0]["effective_to"] < result.iloc[1]["effective_from"]
    assert result.iloc[1]["effective_to"] == pd.Timestamp("2026-08-24")
    assert set(result["taxonomy"]) == {"SW2021"}


def test_sw2021_membership_fails_closed_on_missing_identity_columns():
    with pytest.raises(ValueError, match="missing fields"):
        build_sw2021_industry_intervals(pd.DataFrame([{"ts_code": "600000.SH"}]), coverage_end="2026-08-24")
