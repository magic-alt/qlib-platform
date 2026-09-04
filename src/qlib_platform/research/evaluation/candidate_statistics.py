from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

import numpy as np
import pandas as pd

from qlib_platform.research.diagnostics.features import newey_west_t
from qlib_platform.research.contracts.candidate_program import MultipleTestingSpec, RobustnessSpec
from qlib_platform.research.diagnostics.regime_analysis import benjamini_hochberg, two_sided_normal_p


def empirical_bayes_local_fdr(z_scores: pd.Series) -> pd.Series:
    """Estimate posterior null probability with a deterministic Gaussian KDE.

    The null is N(0, 1). Storey's tail estimator supplies the null proportion,
    while a leave-in Gaussian KDE estimates the observed two-groups density.
    The whole pre-registered primary family must be supplied at once.
    """

    numeric = pd.to_numeric(z_scores, errors="coerce")
    result = pd.Series(float("nan"), index=numeric.index, dtype=float)
    valid = numeric.dropna()
    if len(valid) < 5:
        return result
    values = valid.to_numpy(dtype=float)
    p_values = np.asarray([math.erfc(abs(value) / math.sqrt(2.0)) for value in values])
    pi0 = min(1.0, max(0.05, float((p_values > 0.5).mean() / 0.5)))
    standard_deviation = float(np.std(values, ddof=1))
    bandwidth = max(0.15, 1.06 * standard_deviation * len(values) ** (-0.2))
    differences = (values[:, None] - values[None, :]) / bandwidth
    density = np.exp(-0.5 * differences * differences).mean(axis=1)
    density /= bandwidth * math.sqrt(2.0 * math.pi)
    null_density = np.exp(-0.5 * values * values) / math.sqrt(2.0 * math.pi)
    estimates = np.clip(pi0 * null_density / np.maximum(density, 1e-12), 0.0, 1.0)
    result.loc[valid.index] = estimates
    return result


def _studentized_mean(values: np.ndarray) -> np.ndarray:
    count = np.sum(np.isfinite(values), axis=0)
    means = np.nanmean(values, axis=0)
    std = np.nanstd(values, axis=0, ddof=1)
    denominator = std / np.sqrt(np.maximum(count, 1))
    return np.divide(
        means,
        denominator,
        out=np.full_like(means, np.nan, dtype=float),
        where=(count >= 2) & np.isfinite(denominator) & (denominator > 0),
    )


def romano_wolf_stepdown(
    daily_effects: pd.DataFrame,
    *,
    resamples: int,
    block_sessions: int,
    random_seed: int,
) -> pd.Series:
    """Dependence-aware stepdown p-values using circular moving blocks."""

    if daily_effects.empty or daily_effects.shape[1] == 0:
        raise ValueError("Romano-Wolf requires a non-empty date-by-hypothesis matrix")
    if resamples <= 0 or block_sessions <= 0:
        raise ValueError("Romano-Wolf resamples and block length must be positive")
    numeric = daily_effects.apply(pd.to_numeric, errors="coerce")
    values = numeric.to_numpy(dtype=float)
    observed = np.abs(_studentized_mean(values))
    if not np.isfinite(observed).all():
        raise ValueError("Romano-Wolf hypotheses require at least two non-constant observations")
    centered = values - np.nanmean(values, axis=0)
    rows = len(centered)
    rng = np.random.default_rng(random_seed)
    boot = np.empty((resamples, centered.shape[1]), dtype=float)
    blocks_needed = math.ceil(rows / block_sessions)
    offsets = np.arange(block_sessions)
    for sample in range(resamples):
        starts = rng.integers(0, rows, size=blocks_needed)
        indices = ((starts[:, None] + offsets[None, :]) % rows).reshape(-1)[:rows]
        boot[sample] = np.abs(_studentized_mean(centered[indices]))
    order = np.argsort(-observed, kind="stable")
    adjusted = np.empty(len(order), dtype=float)
    running = 0.0
    for position, hypothesis in enumerate(order):
        remaining = order[position:]
        bootstrap_max = np.nanmax(boot[:, remaining], axis=1)
        raw = float((1 + np.sum(bootstrap_max >= observed[hypothesis])) / (resamples + 1))
        running = max(running, raw)
        adjusted[hypothesis] = min(1.0, running)
    return pd.Series(adjusted, index=numeric.columns, name="romano_wolf_p_value")


def multiple_testing_table(
    daily_effects: pd.DataFrame,
    *,
    spec: MultipleTestingSpec,
) -> pd.DataFrame:
    rows: list[dict[str, float | str]] = []
    for name in daily_effects.columns:
        t_stat = newey_west_t(daily_effects[name], lag=spec.hac_lag)
        rows.append(
            {
                "hypothesis": str(name),
                "hac_t": t_stat,
                "p_value": two_sided_normal_p(t_stat),
            }
        )
    result = pd.DataFrame(rows).set_index("hypothesis")
    result["bh_q_value"] = benjamini_hochberg(result["p_value"])
    result["local_fdr"] = empirical_bayes_local_fdr(result["hac_t"])
    result["romano_wolf_p_value"] = romano_wolf_stepdown(
        daily_effects,
        resamples=spec.romano_wolf_resamples,
        block_sessions=spec.block_sessions,
        random_seed=spec.random_seed,
    )
    result["multiple_testing_pass"] = (
        result["bh_q_value"].le(spec.bh_alpha)
        & result["local_fdr"].le(spec.local_fdr_alpha)
        & result["romano_wolf_p_value"].le(spec.romano_wolf_alpha)
    )
    return result.reset_index()


@dataclass(frozen=True)
class CandidateDecision:
    status: str
    passed: bool
    rejection_reasons: tuple[str, ...]


def _metric(metrics: Mapping[str, object], name: str) -> float:
    return float(str(metrics[name]))


def evaluate_candidate(
    metrics: Mapping[str, object],
    *,
    multiple_testing: MultipleTestingSpec,
    robustness: RobustnessSpec,
) -> CandidateDecision:
    checks = (
        (_metric(metrics, "coverage") >= robustness.minimum_coverage, "COVERAGE"),
        (
            _metric(metrics, "oriented_rank_ic") >= robustness.minimum_oriented_rank_ic,
            "ORIENTED_RANK_IC",
        ),
        (
            _metric(metrics, "positive_fold_ratio") >= robustness.minimum_positive_fold_ratio,
            "POSITIVE_FOLD_RATIO",
        ),
        (abs(_metric(metrics, "hac_t")) >= robustness.minimum_hac_t, "HAC_SIGNIFICANCE"),
        (_metric(metrics, "bh_q_value") <= multiple_testing.bh_alpha, "BH_FDR"),
        (_metric(metrics, "local_fdr") <= multiple_testing.local_fdr_alpha, "LOCAL_FDR"),
        (
            _metric(metrics, "romano_wolf_p_value") <= multiple_testing.romano_wolf_alpha,
            "ROMANO_WOLF",
        ),
        (_metric(metrics, "incremental_rank_ic") > 0, "INCREMENTAL_RANK_IC"),
        (_metric(metrics, "incremental_hac_t") >= robustness.minimum_hac_t, "INCREMENTAL_HAC"),
        (
            _metric(metrics, "worst_fold_rank_ic") >= robustness.minimum_worst_fold_rank_ic,
            "WORST_FOLD",
        ),
        (
            _metric(metrics, "worst_rolling_rank_ic") >= robustness.minimum_worst_rolling_rank_ic,
            "WORST_ROLLING_WINDOW",
        ),
        (
            _metric(metrics, "leave_one_year_min_mean") > 0
            and _metric(metrics, "leave_one_year_retention") >= robustness.minimum_leave_one_year_retention,
            "YEAR_CONCENTRATION",
        ),
        (
            _metric(metrics, "turnover_increase") <= robustness.maximum_turnover_increase,
            "TURNOVER",
        ),
        (_metric(metrics, "stressed_net_spread") > 0, "STRESSED_COST"),
    )
    rejected = tuple(name for passed, name in checks if not passed)
    return CandidateDecision(
        status="RESEARCH_CANDIDATE" if not rejected else "REJECTED",
        passed=not rejected,
        rejection_reasons=rejected,
    )


def nested_ridge_increment(
    baseline_daily_rank_ic: pd.Series,
    candidate_daily_rank_ic: pd.Series,
    *,
    hac_lag: int,
) -> dict[str, float]:
    aligned = pd.concat(
        [
            pd.to_numeric(baseline_daily_rank_ic, errors="coerce").rename("baseline"),
            pd.to_numeric(candidate_daily_rank_ic, errors="coerce").rename("candidate"),
        ],
        axis=1,
        join="inner",
    ).dropna()
    if len(aligned) < 2:
        raise ValueError("nested Ridge comparison requires aligned daily RankIC observations")
    delta = aligned["candidate"] - aligned["baseline"]
    t_stat = newey_west_t(delta, lag=hac_lag)
    return {
        "incremental_rank_ic": float(delta.mean()),
        "incremental_hac_t": t_stat,
        "incremental_p_value": two_sided_normal_p(t_stat),
        "observations": float(len(delta)),
    }
