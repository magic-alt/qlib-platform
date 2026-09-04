from __future__ import annotations

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from qlib_platform.processors import CrossSectionalFactorProcessor, ProcessInfSingleThread


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


def _multifactor_frame(
    dates: list[pd.Timestamp],
    industries: list[float],
    sizes: np.ndarray,
    factors: np.ndarray,
) -> pd.DataFrame:
    instruments = [f"SH{number:06d}" for number in range(len(industries))]
    index = pd.MultiIndex.from_product([dates, instruments], names=["datetime", "instrument"])
    per_date = len(instruments)
    columns = pd.MultiIndex.from_tuples(
        [
            ("feature", "INDUSTRY_L1_CODE"),
            ("feature", "LOG_CIRC_MV"),
            ("feature", "VALUE"),
        ]
    )
    values = np.column_stack(
        [
            np.tile(np.asarray(industries), len(dates)),
            np.tile(sizes, len(dates)),
            np.concatenate([factors + date_index * 100 for date_index in range(len(dates))]),
        ]
    )
    assert len(values) == per_date * len(dates)
    return pd.DataFrame(values, index=index, columns=columns)


def test_cross_sectional_factor_processor_neutralizes_industry_and_size() -> None:
    industries = [801010.0] * 10 + [801020.0] * 10
    within_industry_size = np.linspace(-2.0, 2.0, 10)
    sizes = np.concatenate([within_industry_size, within_industry_size])
    industry_effect = np.asarray([12.0] * 10 + [-7.0] * 10)
    idiosyncratic = np.tile(np.linspace(-1.0, 1.0, 10) ** 2, 2)
    factors = industry_effect + 3.5 * sizes + idiosyncratic
    source = _multifactor_frame([pd.Timestamp("2026-08-03")], industries, sizes, factors)

    actual = CrossSectionalFactorProcessor(minimum_industry_members=5)(source)
    result = actual["feature", "VALUE"]
    industry = pd.Series(industries, index=result.index)
    normalized_size = actual["feature", "LOG_CIRC_MV"]

    assert np.isclose(result.mean(), 0.0, atol=1e-12)
    assert np.isclose(result.std(ddof=0), 1.0, atol=1e-12)
    assert all(np.isclose(group.mean(), 0.0, atol=1e-12) for _, group in result.groupby(industry))
    beta = np.polyfit(normalized_size.to_numpy(), result.to_numpy(), deg=1)[0]
    assert np.isclose(beta, 0.0, atol=1e-12)
    assert ("feature", "INDUSTRY_L1_CODE") not in actual.columns


def test_cross_sectional_factor_processor_skips_small_and_missing_industries() -> None:
    industries = [801010.0] * 5 + [801020.0] * 5 + [801030.0] * 2 + [np.nan] * 2
    sizes = np.tile(np.asarray([-2.0, -1.0, 0.0, 1.0, 2.0]), 3)[: len(industries)]
    factors = np.asarray([10.0] * 5 + [-10.0] * 5 + [30.0] * 2 + [50.0] * 2)
    source = _multifactor_frame([pd.Timestamp("2026-08-03")], industries, sizes, factors)

    actual = CrossSectionalFactorProcessor(minimum_industry_members=5)(source)
    result = actual["feature", "VALUE"]
    industry = pd.Series(industries, index=result.index)

    eligible_means = [result[industry.eq(code)].mean() for code in (801010.0, 801020.0)]
    assert np.isclose(eligible_means[0], eligible_means[1], atol=1e-12)
    assert result[industry.eq(801030.0)].mean() > 0.5
    assert result[industry.isna()].mean() > result[industry.eq(801030.0)].mean()


def test_cross_sectional_factor_processor_preserves_nan_semantics_without_infinities() -> None:
    industries = [801010.0] * 6
    sizes = np.arange(6, dtype=float)
    factors = np.asarray([1.0, 2.0, np.inf, -np.inf, np.nan, 6.0])
    source = _multifactor_frame([pd.Timestamp("2026-08-03")], industries, sizes, factors)

    actual = CrossSectionalFactorProcessor(minimum_industry_members=5)(source)
    result = actual["feature", "VALUE"]

    assert not np.isinf(result.to_numpy()).any()
    assert result.iloc[2:5].isna().all()
    assert result.iloc[[0, 1, 5]].notna().all()


def test_cross_sectional_factor_processor_normalizes_each_date_independently() -> None:
    industries = [801010.0] * 5 + [801020.0] * 5
    sizes = np.tile(np.linspace(-2.0, 2.0, 5), 2)
    factors = np.asarray([0.0, 1.0, 4.0, 9.0, 16.0] * 2)
    dates = [pd.Timestamp("2026-08-03"), pd.Timestamp("2026-08-04")]
    source = _multifactor_frame(dates, industries, sizes, factors)

    actual = CrossSectionalFactorProcessor(minimum_industry_members=5)(source)
    first = actual.xs(dates[0], level="datetime")[("feature", "VALUE")]
    second = actual.xs(dates[1], level="datetime")[("feature", "VALUE")]

    assert np.isclose(first.mean(), 0.0, atol=1e-12)
    assert np.isclose(second.mean(), 0.0, atol=1e-12)
    assert np.isclose(first.std(ddof=0), 1.0, atol=1e-12)
    assert np.isclose(second.std(ddof=0), 1.0, atol=1e-12)
    np.testing.assert_allclose(first.to_numpy(), second.to_numpy(), atol=1e-12)
