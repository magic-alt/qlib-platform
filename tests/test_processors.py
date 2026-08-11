from __future__ import annotations

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from tushare_qlib.processors import ProcessInfSingleThread


def test_process_inf_replaces_each_date_with_its_finite_column_mean() -> None:
    index = pd.MultiIndex.from_tuples(
        [
            (pd.Timestamp("2026-08-04"), "SZ000002"),
            (pd.Timestamp("2026-08-03"), "SH600000"),
            (pd.Timestamp("2026-08-04"), "SH600000"),
            (pd.Timestamp("2026-08-03"), "SZ000002"),
        ],
        names=["datetime", "instrument"],
    )
    source = pd.DataFrame(
        {"feature_a": [np.inf, 1.0, 4.0, -np.inf], "feature_b": [2.0, np.inf, -np.inf, np.nan]},
        index=index,
    )

    actual = ProcessInfSingleThread()(source.copy())
    expected = pd.DataFrame(
        {"feature_a": [1.0, 1.0, 4.0, 4.0], "feature_b": [np.nan, np.nan, 2.0, 2.0]},
        index=pd.MultiIndex.from_tuples(
            [
                (pd.Timestamp("2026-08-03"), "SH600000"),
                (pd.Timestamp("2026-08-03"), "SZ000002"),
                (pd.Timestamp("2026-08-04"), "SH600000"),
                (pd.Timestamp("2026-08-04"), "SZ000002"),
            ],
            names=["datetime", "instrument"],
        ),
    )

    assert_frame_equal(actual, expected)


def test_process_inf_handles_batched_multiindex_columns() -> None:
    index = pd.MultiIndex.from_product(
        [[pd.Timestamp("2026-08-03")], ["SH600000", "SZ000002"]],
        names=["datetime", "instrument"],
    )
    columns = pd.MultiIndex.from_product([["feature"], [f"f{number}" for number in range(17)]])
    source = pd.DataFrame(np.ones((2, 17)), index=index, columns=columns)
    source.loc[(pd.Timestamp("2026-08-03"), "SH600000"), ("feature", "f16")] = np.inf

    actual = ProcessInfSingleThread()(source)

    assert actual.loc[(pd.Timestamp("2026-08-03"), "SH600000"), ("feature", "f16")] == 1.0
