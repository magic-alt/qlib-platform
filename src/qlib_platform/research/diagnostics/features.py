from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping

import numpy as np
import pandas as pd

from qlib_platform.research.features.taxonomy import FactorTaxonomy


@dataclass(frozen=True)
class FeatureDiagnosticsSpec:
    schema_version: str = "feature_diagnostics_v1"
    min_cross_section: int = 50
    rolling_sessions: int = 252
    short_rolling_sessions: int = 63
    quantiles: int = 5
    correlation_threshold: float = 0.85

    def __post_init__(self) -> None:
        if self.min_cross_section < 2:
            raise ValueError("min_cross_section must be at least 2")
        if min(self.rolling_sessions, self.short_rolling_sessions) < 2:
            raise ValueError("rolling windows must be at least 2 sessions")
        if self.quantiles < 3:
            raise ValueError("quantiles must be at least 3")
        if not 0 < self.correlation_threshold <= 1:
            raise ValueError("correlation_threshold must be in (0, 1]")

    def to_manifest(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class FeatureDiagnosticArtifacts:
    daily: pd.DataFrame
    fold: pd.DataFrame
    yearly: pd.DataFrame
    rolling: pd.DataFrame
    summary: pd.DataFrame
    quantiles: pd.DataFrame


def feature_columns(frame: pd.DataFrame) -> pd.DataFrame:
    if isinstance(frame.columns, pd.MultiIndex):
        top = frame.columns.get_level_values(0)
        if "feature" not in top:
            raise ValueError("FeatureSnapshot has no feature column group")
        result = frame["feature"].copy()
    else:
        result = frame.copy()
    result.columns = [str(value) for value in result.columns]
    if len(result.columns) != len(set(result.columns)):
        raise ValueError("FeatureSnapshot contains duplicate feature names")
    return result


def normalize_oos_labels(labels: pd.Series | pd.DataFrame) -> pd.DataFrame:
    frame = labels.to_frame("label") if isinstance(labels, pd.Series) else labels.copy()
    if not isinstance(frame.index, pd.MultiIndex) or frame.index.names != ["datetime", "instrument"]:
        raise ValueError("OOS labels require a datetime/instrument MultiIndex")
    if frame.index.has_duplicates:
        raise ValueError("OOS labels contain duplicate datetime/instrument keys")
    if "label" not in frame:
        if len(frame.columns) != 1:
            raise ValueError("OOS labels must contain exactly one label column")
        frame = frame.rename(columns={frame.columns[0]: "label"})
    frame = frame[["label"]].sort_index()
    frame["label"] = pd.to_numeric(frame["label"], errors="coerce")
    return frame


def align_oos_features(features: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(features.index, pd.MultiIndex) or features.index.names != [
        "datetime",
        "instrument",
    ]:
        raise ValueError("FeatureSnapshot requires a datetime/instrument MultiIndex")
    if features.index.has_duplicates:
        raise ValueError("FeatureSnapshot contains duplicate datetime/instrument keys")
    missing = labels.index.difference(features.index)
    if len(missing):
        raise ValueError(f"FeatureSnapshot is missing {len(missing)} rolling OOS keys")
    aligned = features.reindex(labels.index)
    if not aligned.index.equals(labels.index):
        raise ValueError("FeatureSnapshot reindex did not preserve the rolling OOS index")
    return aligned


def _taxonomy_fields(taxonomy: FactorTaxonomy, feature: str) -> dict[str, object]:
    entry = taxonomy.entry(feature)
    return {
        "family": entry.family,
        "role": entry.role,
        "direction": entry.direction,
        "ranking_eligible": entry.ranking_eligible,
    }


def _safe_corr(left: pd.Series, right: pd.Series, *, method: str = "pearson") -> float:
    if len(left) < 2 or left.nunique(dropna=True) < 2 or right.nunique(dropna=True) < 2:
        return float("nan")
    return float(left.corr(right, method=method))


def derive_feature_daily_diagnostics(
    features: pd.DataFrame,
    labels: pd.DataFrame,
    taxonomy: FactorTaxonomy,
    spec: FeatureDiagnosticsSpec,
) -> pd.DataFrame:
    numeric = features.apply(pd.to_numeric, errors="coerce")
    label = labels["label"]
    rows: list[dict[str, object]] = []
    for date, block in numeric.groupby(level="datetime", sort=True):
        block = block.droplevel("datetime")
        day_label = label.xs(date, level="datetime").reindex(block.index)
        universe_count = len(block)
        label_values = day_label.to_numpy(dtype=float)
        label_finite = np.isfinite(label_values)
        label_valid_count = int(label_finite.sum())
        for name in numeric.columns:
            raw = block[name]
            values = raw.to_numpy(dtype=float)
            finite = np.isfinite(values)
            pair = finite & label_finite
            valid_count = int(pair.sum())
            feature_valid_count = int(finite.sum())
            missing_count = int(raw.isna().sum())
            nonfinite_count = int((~finite & ~raw.isna().to_numpy()).sum())
            paired_feature = pd.Series(values[pair])
            paired_label = pd.Series(label_values[pair])
            eligible = valid_count >= spec.min_cross_section
            std = float(paired_feature.std(ddof=1)) if valid_count > 1 else float("nan")
            ic = _safe_corr(paired_feature, paired_label) if eligible else float("nan")
            rank_ic = (
                _safe_corr(paired_feature, paired_label, method="spearman") if eligible else float("nan")
            )
            rows.append(
                {
                    "date": pd.Timestamp(date).normalize(),
                    "feature": str(name),
                    **_taxonomy_fields(taxonomy, str(name)),
                    "universe_count": universe_count,
                    "label_valid_count": label_valid_count,
                    "feature_valid_count": feature_valid_count,
                    "valid_count": valid_count,
                    "coverage": valid_count / universe_count if universe_count else float("nan"),
                    "missing_rate": missing_count / universe_count if universe_count else float("nan"),
                    "nonfinite_rate": (nonfinite_count / universe_count if universe_count else float("nan")),
                    "feature_std": std,
                    "ic": ic,
                    "rank_ic": rank_ic,
                }
            )
    return pd.DataFrame(rows).sort_values(["date", "feature"], kind="stable").reset_index(drop=True)


def _ratio(values: pd.Series) -> float:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if len(clean) < 2:
        return float("nan")
    std = float(clean.std(ddof=1))
    return float(clean.mean() / std) if np.isfinite(std) and std > 0 else float("nan")


def _positive_ratio(values: pd.Series) -> float:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    return float(clean.gt(0).mean()) if len(clean) else float("nan")


def _wilson(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if total <= 0:
        return float("nan"), float("nan")
    rate = successes / total
    denominator = 1.0 + z * z / total
    center = (rate + z * z / (2.0 * total)) / denominator
    half = z * np.sqrt(rate * (1.0 - rate) / total + z * z / (4.0 * total * total))
    half /= denominator
    return float(center - half), float(center + half)


def newey_west_t(values: pd.Series, *, lag: int) -> float:
    clean = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    count = len(clean)
    if count < 2 or lag < 0:
        return float("nan")
    lag = min(lag, count - 1)
    residual = clean - clean.mean()
    long_run = float(np.dot(residual, residual) / count)
    for offset in range(1, lag + 1):
        gamma = float(np.dot(residual[offset:], residual[:-offset]) / count)
        long_run += 2.0 * (1.0 - offset / (lag + 1.0)) * gamma
    if not np.isfinite(long_run) or long_run <= 0:
        return float("nan")
    standard_error = np.sqrt(long_run / count)
    return float(clean.mean() / standard_error) if standard_error > 0 else float("nan")


def _sign_persistence(values: pd.Series) -> float:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    clean = clean.loc[clean.ne(0)]
    if len(clean) < 2:
        return float("nan")
    signs = np.sign(clean.to_numpy(dtype=float))
    return float((signs[1:] == signs[:-1]).mean())


def _cumulative_drawdown(values: pd.Series) -> float:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if clean.empty:
        return float("nan")
    cumulative = clean.cumsum()
    return float((cumulative - cumulative.cummax()).min())


def _clean_median(values: pd.Series) -> float:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    return float(clean.median()) if len(clean) else float("nan")


def _clean_quantile(values: pd.Series, quantile: float) -> float:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    return float(clean.quantile(quantile)) if len(clean) else float("nan")


def _period_metrics(
    daily: pd.DataFrame,
    assignments: Mapping[pd.Timestamp, str] | None,
    *,
    period_name: str,
) -> pd.DataFrame:
    frame = daily.copy()
    if assignments is None:
        frame[period_name] = frame["date"].dt.year.astype(int)
    else:
        frame[period_name] = frame["date"].map(assignments)
        if frame[period_name].isna().any():
            missing = sorted(
                str(value.date()) for value in frame.loc[frame[period_name].isna(), "date"].unique()
            )
            raise ValueError(f"rolling OOS dates are absent from fold assignments: {missing[:5]}")
    rows: list[dict[str, object]] = []
    for (period, feature), block in frame.groupby([period_name, "feature"], sort=True):
        rows.append(
            {
                period_name: period,
                "feature": feature,
                "sessions": int(block["date"].nunique()),
                "valid_ic_days": int(block["ic"].notna().sum()),
                "valid_rank_ic_days": int(block["rank_ic"].notna().sum()),
                "ic_mean": float(block["ic"].mean()),
                "rank_ic_mean": float(block["rank_ic"].mean()),
                "icir": _ratio(block["ic"]),
                "rank_icir": _ratio(block["rank_ic"]),
            }
        )
    return pd.DataFrame(rows).sort_values([period_name, "feature"], kind="stable").reset_index(drop=True)


def _rolling_metrics(daily: pd.DataFrame, window: int) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for feature, block in daily.groupby("feature", sort=True):
        ordered = block.sort_values("date", kind="stable").reset_index(drop=True)
        result = pd.DataFrame(
            {
                "date": ordered["date"],
                "feature": feature,
                "window_sessions": window,
                "valid_ic_days": ordered["ic"].rolling(window, min_periods=1).count(),
                "valid_rank_ic_days": ordered["rank_ic"].rolling(window, min_periods=1).count(),
                "rolling_ic_mean": ordered["ic"].rolling(window, min_periods=1).mean(),
                "rolling_rank_ic_mean": ordered["rank_ic"].rolling(window, min_periods=1).mean(),
            }
        )
        if len(result):
            result.loc[result.index < window - 1, result.columns[3:]] = np.nan
        rows.append(result)
    return (
        pd.concat(rows, ignore_index=True)
        .sort_values(["date", "feature"], kind="stable")
        .reset_index(drop=True)
    )


def _orientation(taxonomy: FactorTaxonomy, feature: str) -> float | None:
    return taxonomy.entry(feature).orientation


def derive_feature_summary(
    daily: pd.DataFrame,
    fold: pd.DataFrame,
    yearly: pd.DataFrame,
    rolling: pd.DataFrame,
    taxonomy: FactorTaxonomy,
    spec: FeatureDiagnosticsSpec,
    *,
    hac_lag: int,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for feature, block in daily.groupby("feature", sort=True):
        block = block.sort_values("date", kind="stable")
        ic = block["ic"]
        rank_ic = block["rank_ic"]
        feature_folds = fold.loc[fold["feature"].eq(feature)]
        feature_years = yearly.loc[yearly["feature"].eq(feature)]
        feature_rolling = rolling.loc[rolling["feature"].eq(feature)]
        fold_ic = feature_folds["ic_mean"].dropna()
        fold_rank = feature_folds["rank_ic_mean"].dropna()
        ic_success = int(fold_ic.gt(0).sum())
        rank_success = int(fold_rank.gt(0).sum())
        ic_wilson = _wilson(ic_success, len(fold_ic))
        rank_wilson = _wilson(rank_success, len(fold_rank))
        orientation = _orientation(taxonomy, feature)
        oriented_ic = ic * orientation if orientation is not None else pd.Series(dtype=float)
        oriented_rank = rank_ic * orientation if orientation is not None else pd.Series(dtype=float)
        oriented_fold_ic = fold_ic * orientation if orientation is not None else pd.Series(dtype=float)
        oriented_fold_rank = fold_rank * orientation if orientation is not None else pd.Series(dtype=float)
        short_ic = ic.rolling(spec.short_rolling_sessions, min_periods=1).mean()
        short_rank = rank_ic.rolling(spec.short_rolling_sessions, min_periods=1).mean()
        short_ic.iloc[: spec.short_rolling_sessions - 1] = np.nan
        short_rank.iloc[: spec.short_rolling_sessions - 1] = np.nan
        entry = taxonomy.entry(feature)
        rows.append(
            {
                "feature": feature,
                **_taxonomy_fields(taxonomy, feature),
                "daily_sessions": int(block["date"].nunique()),
                "valid_ic_days": int(ic.notna().sum()),
                "valid_rank_ic_days": int(rank_ic.notna().sum()),
                "ic_mean": float(ic.mean()),
                "rank_ic_mean": float(rank_ic.mean()),
                "ic_std": float(ic.std(ddof=1)),
                "rank_ic_std": float(rank_ic.std(ddof=1)),
                "icir": _ratio(ic),
                "rank_icir": _ratio(rank_ic),
                "oriented_icir": (_ratio(oriented_ic) if orientation is not None else float("nan")),
                "oriented_rank_icir": (_ratio(oriented_rank) if orientation is not None else float("nan")),
                "oriented_ic_mean": float(oriented_ic.mean()) if orientation is not None else float("nan"),
                "oriented_rank_ic_mean": (
                    float(oriented_rank.mean()) if orientation is not None else float("nan")
                ),
                "positive_ic_day_ratio": _positive_ratio(ic),
                "positive_rank_ic_day_ratio": _positive_ratio(rank_ic),
                "positive_ic_fold_ratio": ic_success / len(fold_ic) if len(fold_ic) else float("nan"),
                "positive_ic_fold_wilson_low": ic_wilson[0],
                "positive_ic_fold_wilson_high": ic_wilson[1],
                "positive_rank_ic_fold_ratio": (
                    rank_success / len(fold_rank) if len(fold_rank) else float("nan")
                ),
                "positive_rank_ic_fold_wilson_low": rank_wilson[0],
                "positive_rank_ic_fold_wilson_high": rank_wilson[1],
                "positive_oriented_ic_fold_ratio": (
                    float(oriented_fold_ic.gt(0).mean()) if orientation is not None else float("nan")
                ),
                "positive_oriented_rank_ic_fold_ratio": (
                    float(oriented_fold_rank.gt(0).mean()) if orientation is not None else float("nan")
                ),
                "positive_year_ratio": float(feature_years["ic_mean"].dropna().gt(0).mean()),
                "positive_rank_ic_year_ratio": float(feature_years["rank_ic_mean"].dropna().gt(0).mean()),
                "worst_year_ic": float(feature_years["ic_mean"].min()),
                "worst_year_rank_ic": float(feature_years["rank_ic_mean"].min()),
                "rolling_12m_min": float(feature_rolling["rolling_ic_mean"].min()),
                "rolling_12m_median": _clean_median(feature_rolling["rolling_ic_mean"]),
                "rolling_12m_rank_ic_min": float(feature_rolling["rolling_rank_ic_mean"].min()),
                "rolling_12m_rank_ic_median": _clean_median(feature_rolling["rolling_rank_ic_mean"]),
                "worst_63d_ic": float(short_ic.min()),
                "worst_63d_rank_ic": float(short_rank.min()),
                "ic_cumulative_drawdown": _cumulative_drawdown(ic),
                "rank_ic_cumulative_drawdown": _cumulative_drawdown(rank_ic),
                "ic_sign_persistence": _sign_persistence(ic),
                "rank_ic_sign_persistence": _sign_persistence(rank_ic),
                "ic_p10": _clean_quantile(ic, 0.10),
                "ic_median": _clean_median(ic),
                "ic_p90": _clean_quantile(ic, 0.90),
                "rank_ic_p10": _clean_quantile(rank_ic, 0.10),
                "rank_ic_median": _clean_median(rank_ic),
                "rank_ic_p90": _clean_quantile(rank_ic, 0.90),
                "coverage_median": float(block["coverage"].median()),
                "coverage_p10": float(block["coverage"].quantile(0.10)),
                "missing_rate": float(block["missing_rate"].mean()),
                "nonfinite_rate": float(block["nonfinite_rate"].mean()),
                "ic_hac_t": newey_west_t(ic, lag=hac_lag),
                "rank_ic_hac_t": newey_west_t(rank_ic, lag=hac_lag),
                "orientation_available": entry.orientation is not None,
            }
        )
    return pd.DataFrame(rows).sort_values("feature", kind="stable").reset_index(drop=True)


def derive_factor_quantile_returns(
    features: pd.DataFrame,
    labels: pd.DataFrame,
    taxonomy: FactorTaxonomy,
    spec: FeatureDiagnosticsSpec,
) -> pd.DataFrame:
    numeric = features.apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan)
    dates = pd.DatetimeIndex(labels.index.get_level_values("datetime").unique()).sort_values()
    rows: list[dict[str, object]] = []
    for feature in sorted(numeric.columns):
        previous_top: set[str] | None = None
        previous_bottom: set[str] | None = None
        entry = taxonomy.entry(str(feature))
        for date in dates:
            values = numeric[feature].xs(date, level="datetime")
            day_label = labels["label"].xs(date, level="datetime").reindex(values.index)
            paired = pd.DataFrame({"value": values, "label": day_label}).dropna()
            base: dict[str, object] = {
                "date": pd.Timestamp(date).normalize(),
                "feature": str(feature),
                **_taxonomy_fields(taxonomy, str(feature)),
                "valid_count": len(paired),
            }
            quantile_means = {f"q{number}_return": float("nan") for number in range(1, spec.quantiles + 1)}
            counts = {f"q{number}_count": 0 for number in range(1, spec.quantiles + 1)}
            valid = len(paired) >= spec.min_cross_section and paired["value"].nunique() >= spec.quantiles
            if not valid:
                previous_top = None
                previous_bottom = None
                rows.append(
                    {
                        **base,
                        **quantile_means,
                        **counts,
                        "raw_q5_minus_q1": float("nan"),
                        "oriented_long_short": float("nan"),
                        "monotonicity": float("nan"),
                        "top_quantile_turnover": float("nan"),
                        "bottom_quantile_turnover": float("nan"),
                    }
                )
                continue
            percentile = paired["value"].rank(method="average", pct=True)
            paired["quantile"] = np.ceil(percentile * spec.quantiles).clip(1, spec.quantiles).astype(int)
            grouped = paired.groupby("quantile", sort=True)["label"]
            means = grouped.mean()
            sizes = grouped.size()
            for number in range(1, spec.quantiles + 1):
                if number in means.index:
                    quantile_means[f"q{number}_return"] = float(means.loc[number])
                    counts[f"q{number}_count"] = int(sizes.loc[number])
            bottom = {str(value) for value in paired.index[paired["quantile"].eq(1)]}
            top = {str(value) for value in paired.index[paired["quantile"].eq(spec.quantiles)]}
            top_turnover = (
                1.0 - len(top.intersection(previous_top)) / len(previous_top)
                if previous_top
                else float("nan")
            )
            bottom_turnover = (
                1.0 - len(bottom.intersection(previous_bottom)) / len(previous_bottom)
                if previous_bottom
                else float("nan")
            )
            previous_top = top
            previous_bottom = bottom
            raw_spread = quantile_means[f"q{spec.quantiles}_return"] - quantile_means["q1_return"]
            orientation = entry.orientation
            populated = means.dropna()
            monotonicity = (
                float(
                    pd.Series(populated.index, dtype=float).corr(
                        populated.reset_index(drop=True), method="spearman"
                    )
                )
                if len(populated) >= 3
                else float("nan")
            )
            rows.append(
                {
                    **base,
                    **quantile_means,
                    **counts,
                    "raw_q5_minus_q1": raw_spread,
                    "oriented_long_short": (
                        raw_spread * orientation if orientation is not None else float("nan")
                    ),
                    "monotonicity": monotonicity,
                    "top_quantile_turnover": top_turnover,
                    "bottom_quantile_turnover": bottom_turnover,
                }
            )
    return pd.DataFrame(rows).sort_values(["date", "feature"], kind="stable").reset_index(drop=True)


def add_quantile_summary(summary: pd.DataFrame, quantiles: pd.DataFrame) -> pd.DataFrame:
    aggregate = (
        quantiles.groupby("feature", sort=True)
        .agg(
            raw_q5_minus_q1_mean=("raw_q5_minus_q1", "mean"),
            oriented_long_short_mean=("oriented_long_short", "mean"),
            quantile_monotonicity_mean=("monotonicity", "mean"),
            positive_raw_spread_ratio=("raw_q5_minus_q1", lambda values: float(values.dropna().gt(0).mean())),
            top_quantile_turnover_mean=("top_quantile_turnover", "mean"),
            bottom_quantile_turnover_mean=("bottom_quantile_turnover", "mean"),
        )
        .reset_index()
    )
    return (
        summary.merge(aggregate, on="feature", how="left", validate="one_to_one")
        .sort_values("feature", kind="stable")
        .reset_index(drop=True)
    )


def build_feature_diagnostics(
    features: pd.DataFrame,
    labels: pd.DataFrame,
    taxonomy: FactorTaxonomy,
    spec: FeatureDiagnosticsSpec,
    *,
    fold_assignments: Mapping[pd.Timestamp, str],
    hac_lag: int,
) -> FeatureDiagnosticArtifacts:
    normalized_labels = normalize_oos_labels(labels)
    normalized_features = feature_columns(features)
    taxonomy_features = list(normalized_features.columns)
    if set(taxonomy_features) != set(taxonomy.entries):
        raise ValueError("taxonomy and FeatureSnapshot columns differ")
    aligned = align_oos_features(normalized_features, normalized_labels)
    daily = derive_feature_daily_diagnostics(aligned, normalized_labels, taxonomy, spec)
    fold = _period_metrics(daily, fold_assignments, period_name="fold")
    yearly = _period_metrics(daily, None, period_name="year")
    rolling = _rolling_metrics(daily, spec.rolling_sessions)
    quantiles = derive_factor_quantile_returns(aligned, normalized_labels, taxonomy, spec)
    summary = derive_feature_summary(
        daily,
        fold,
        yearly,
        rolling,
        taxonomy,
        spec,
        hac_lag=hac_lag,
    )
    summary = add_quantile_summary(summary, quantiles)
    return FeatureDiagnosticArtifacts(daily, fold, yearly, rolling, summary, quantiles)
