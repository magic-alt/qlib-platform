from __future__ import annotations

import numpy as np
import pandas as pd

from qlib_platform.research.contracts.candidate_program import load_candidate_contract
from qlib_platform.research.evaluation.candidate_statistics import (
    empirical_bayes_local_fdr,
    evaluate_candidate,
    multiple_testing_table,
    nested_ridge_increment,
    romano_wolf_stepdown,
)


def _specs():
    contract = load_candidate_contract("configs/research/ashare_candidate_research_v1.yaml")
    return contract.multiple_testing, contract.robustness


def test_local_fdr_orders_strong_discovery_below_nulls():
    values = pd.Series([0.0, 0.1, -0.2, 0.3, -0.4, 5.0], index=list("abcdef"))
    result = empirical_bayes_local_fdr(values)
    assert result["f"] < result[["a", "b", "c", "d", "e"]].median()
    assert result.between(0, 1).all()


def test_romano_wolf_is_deterministic_and_dependence_aware():
    rng = np.random.default_rng(7)
    common = rng.normal(0, 0.01, 160)
    daily = pd.DataFrame(
        {
            "strong_a": common + 0.02,
            "strong_b": common + rng.normal(0, 0.002, 160) + 0.02,
            "null": rng.normal(0, 0.01, 160),
        }
    )
    first = romano_wolf_stepdown(daily, resamples=300, block_sessions=10, random_seed=42)
    second = romano_wolf_stepdown(daily, resamples=300, block_sessions=10, random_seed=42)
    pd.testing.assert_series_equal(first, second)
    assert first["strong_a"] < first["null"]


def test_multiple_testing_table_preserves_one_predeclared_family():
    spec, _ = _specs()
    spec = type(spec)(**{**spec.__dict__, "romano_wolf_resamples": 200})
    rng = np.random.default_rng(8)
    daily = pd.DataFrame(
        {f"H{number:03d}": rng.normal(0.03 if number < 6 else 0.0, 0.02, 180) for number in range(11)}
    )
    result = multiple_testing_table(daily, spec=spec)
    assert set(result["hypothesis"]) == set(daily)
    assert {"bh_q_value", "local_fdr", "romano_wolf_p_value", "multiple_testing_pass"}.issubset(result)


def test_candidate_gate_records_first_class_rejection_reasons():
    testing, robustness = _specs()
    passing = {
        "coverage": 0.9,
        "oriented_rank_ic": 0.02,
        "positive_fold_ratio": 0.8,
        "hac_t": 2.5,
        "bh_q_value": 0.01,
        "local_fdr": 0.05,
        "romano_wolf_p_value": 0.02,
        "incremental_rank_ic": 0.005,
        "incremental_hac_t": 2.1,
        "worst_fold_rank_ic": -0.005,
        "worst_rolling_rank_ic": -0.001,
        "leave_one_year_min_mean": 0.001,
        "leave_one_year_retention": 0.7,
        "turnover_increase": 0.1,
        "stressed_net_spread": 0.001,
    }
    assert evaluate_candidate(passing, multiple_testing=testing, robustness=robustness).passed
    passing["stressed_net_spread"] = -0.001
    rejected = evaluate_candidate(passing, multiple_testing=testing, robustness=robustness)
    assert rejected.status == "REJECTED"
    assert rejected.rejection_reasons == ("STRESSED_COST",)


def test_nested_ridge_increment_is_computed_on_aligned_daily_oos():
    baseline = pd.Series([0.00, 0.01, 0.02, 0.01])
    candidate = pd.Series([0.01, 0.02, 0.03, 0.02])
    result = nested_ridge_increment(baseline, candidate, hac_lag=1)
    assert result["incremental_rank_ic"] == np.mean(candidate - baseline)
    assert result["observations"] == 4
