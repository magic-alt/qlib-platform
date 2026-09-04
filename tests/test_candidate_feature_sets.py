from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qlib_platform.alpha.registry import ALPHA_PACKS
from qlib_platform.data.fundamentals import PIT_FIELDS_V2
from qlib_platform.research.features.candidate_sets import (
    BENCHMARK_FAMILIES,
    EXPERIMENT_MATRIX,
    FEATURE_SETS,
    HYPOTHESIS_FEATURE_SETS,
    build_benchmark_factors,
    build_explicit_interactions,
    residualize_lowvol,
    select_cluster_representative,
)
from qlib_platform.data.processors import Phase2FeatureSetProcessor


def _raw() -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-01", periods=260)
    instruments = ["A", "B", "C", "D"]
    index = pd.MultiIndex.from_product([dates, instruments], names=["datetime", "instrument"])
    row = np.arange(len(index), dtype=float)
    per_instrument = (
        np.tile(np.arange(len(dates), dtype=float), len(instruments))
        .reshape(len(instruments), len(dates))
        .T.reshape(-1)
    )
    return pd.DataFrame(
        {
            "close": 10.0 + per_instrument * 0.01 + np.tile([0.0, 1.0, 2.0, 3.0], len(dates)),
            "money": 1_000_000.0 + row,
            "turnover_rate_f": 1.0 + row / 100_000,
            "total_mv": 100.0 + np.tile([10.0, 20.0, 30.0, 40.0], len(dates)),
            "dv_ttm": np.tile([1.0, 2.0, 3.0, 4.0], len(dates)),
            "industry_l1_code": np.tile(["801010", "801010", "801780", "801020"], len(dates)),
            "roe_waa_pit": 0.10 + row / 1_000_000,
            "roa_pit": 0.05 + row / 2_000_000,
            "total_assets_pit": 200.0 + row / 100,
            "prior_year_total_assets_pit": 180.0 + row / 100,
            "total_equity_pit": 80.0 + row / 200,
            "gross_profit_ttm_pit": 40.0 + row / 500,
            "operating_profit_ttm_pit": 30.0 + row / 600,
            "prior_year_operating_profit_ttm_pit": 27.0 + row / 700,
            "operating_cash_flow_ttm_pit": 25.0 + row / 700,
            "prior_year_operating_cash_flow_ttm_pit": 22.0 + row / 800,
            "revenue_ttm_pit": 120.0 + row / 100,
            "prior_year_revenue_ttm_pit": 100.0 + row / 100,
            "parent_net_income_ttm_pit": 20.0 + row / 1000,
            "prior_year_parent_net_income_ttm_pit": 18.0 + row / 1000,
            "capex_ttm_pit": 8.0 + row / 5000,
        },
        index=index,
    )


def test_v2_alpha_packs_require_expanded_pit_and_industry():
    pack = ALPHA_PACKS["ashare_alpha_phase2_v1"]
    assert set(PIT_FIELDS_V2).issubset(pack.required_qlib_fields)
    assert set(pack.required_release_components) == {
        "pit_fundamentals",
        "industry_classification_pit",
    }
    assert pack.warmup_trading_days == 252


def test_benchmark_formulas_and_financial_applicability():
    raw = _raw()
    result = build_benchmark_factors(raw, non_applicable_industry_codes={"801780"})
    first = result.iloc[0]
    source = raw.iloc[0]

    assert first["EARNINGS_YIELD"] == pytest.approx(source["parent_net_income_ttm_pit"] / source["total_mv"])
    assert first["GROSS_PROFIT_ASSETS"] == pytest.approx(
        source["gross_profit_ttm_pit"]
        / ((source["total_assets_pit"] + source["prior_year_total_assets_pit"]) / 2)
    )
    financial = result.xs("C", level="instrument")
    assert financial["GROSS_PROFIT_ASSETS"].isna().all()
    assert financial["EARNINGS_YIELD"].notna().all()
    assert result["VOL_20"].notna().any()
    assert result["MOMENTUM_12M"].notna().any()


def test_explicit_interactions_are_predeclared_daily_standardized():
    factors = build_benchmark_factors(_raw())
    interactions = build_explicit_interactions(factors)

    assert len(interactions.columns) == 6
    for _, block in interactions.dropna().groupby(level="datetime"):
        assert np.allclose(block.mean().to_numpy(), 0.0, atol=1e-12)


def test_lowvol_residual_is_orthogonal_to_registered_controls():
    dates = [pd.Timestamp("2026-01-05")]
    instruments = [f"S{number:03d}" for number in range(60)]
    index = pd.MultiIndex.from_product([dates, instruments], names=["datetime", "instrument"])
    value = np.linspace(-2, 2, len(index))
    profitability = np.sin(np.linspace(0, 4, len(index)))
    size = np.cos(np.linspace(0, 3, len(index)))
    idiosyncratic = np.linspace(-1, 1, len(index)) ** 2
    composites = pd.DataFrame(
        {
            "VALUE_COMPOSITE": value,
            "PROFITABILITY_COMPOSITE": profitability,
            "SIZE_COMPOSITE": size,
            "LOWVOL_COMPOSITE": 2 * value - profitability + 3 * size + idiosyncratic,
        },
        index=index,
    )
    industries = pd.Series(["A"] * 30 + ["B"] * 30, index=index)
    residual = residualize_lowvol(composites, industries, minimum_cross_section=50)
    raw_residual = residual.to_numpy()

    assert np.isfinite(raw_residual).all()
    assert abs(pd.Series(raw_residual).corr(pd.Series(value), method="spearman")) < 0.15


def test_ablation_and_experiment_matrix_are_fixed():
    assert list(FEATURE_SETS)[:8] == [f"A{number}" for number in range(8)]
    assert set(FEATURE_SETS) == {
        *(f"A{number}" for number in range(8)),
        "VP1",
        "LVR1",
        "I1",
    }
    assert list(EXPERIMENT_MATRIX) == [f"P2-{number:02d}" for number in range(1, 11)]
    assert EXPERIMENT_MATRIX["P2-04"] == ("A3", "xgboost")
    assert EXPERIMENT_MATRIX["P2-05"] == ("VP1", "ridge")
    assert EXPERIMENT_MATRIX["P2-09"] == ("A7", "ridge")
    assert FEATURE_SETS["A7"].include_selected_technical is True


def test_cluster_representative_selection_is_deterministic():
    summary = pd.DataFrame(
        {
            "feature": ["B", "A", "C"],
            "gate_pass": [True, True, False],
            "oriented_rank_ic": [0.02, 0.02, 0.10],
            "turnover": [0.30, 0.20, 0.01],
            "coverage": [0.90, 0.90, 1.0],
        }
    )
    assert select_cluster_representative(summary) == "A"


def test_phase2_processor_isolates_registered_feature_sets():
    factors = build_benchmark_factors(_raw()).dropna()
    factors["INDUSTRY_L1_CODE"] = "801010"
    for support, value in {
        "PAUSED": 0.0,
        "IS_ST": 0.0,
        "LISTED_DAYS": 500.0,
        "CIRC_MV": 10_000_000_000.0,
        "MONEY20": 100_000_000.0,
    }.items():
        factors[support] = value
    factors.columns = pd.MultiIndex.from_product([["feature"], factors.columns])

    value = Phase2FeatureSetProcessor("A1")(factors)
    value_profitability = Phase2FeatureSetProcessor("VP1")(factors)
    residual = Phase2FeatureSetProcessor("LVR1", minimum_residual_cross_section=5)(factors)
    h003_baseline = Phase2FeatureSetProcessor("H003_BASELINE")(factors)
    h003_candidate = Phase2FeatureSetProcessor("H003_CANDIDATE")(factors)
    h104_baseline = Phase2FeatureSetProcessor("H104_BASELINE")(factors)
    h104_candidate = Phase2FeatureSetProcessor("H104_CANDIDATE")(factors)

    assert list(value["feature"]) == ["EARNINGS_YIELD", "BOOK_TO_PRICE", "DIVIDEND_YIELD"]
    assert set(value_profitability["feature"]) == {
        *BENCHMARK_FAMILIES["Value"],
        *BENCHMARK_FAMILIES["Profitability"],
    }
    assert list(residual["feature"]) == ["LOWVOL_RESIDUAL"]
    assert list(h003_candidate["feature"]) == [
        *h003_baseline["feature"].columns,
        "GROSS_PROFIT_ASSETS",
    ]
    assert list(h104_candidate["feature"]) == [
        *h104_baseline["feature"].columns,
        "PROFITABILITY_X_LOWVOL",
    ]
    assert len(HYPOTHESIS_FEATURE_SETS) == 22
    with pytest.raises(ValueError, match="selected_technical"):
        Phase2FeatureSetProcessor("A7")
