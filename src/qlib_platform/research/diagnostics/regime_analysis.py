from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from qlib_platform.research.features.taxonomy import FactorTaxonomy
from qlib_platform.research.diagnostics.features import newey_west_t, normalize_oos_labels
from qlib_platform.research.diagnostics.regimes import RegimeSpec


@dataclass(frozen=True)
class RegimeDiagnosticArtifacts:
    labels: pd.DataFrame
    factor_regime: pd.DataFrame
    model_regime: pd.DataFrame
    model_factor_correlation: pd.DataFrame
    topk_overlap: pd.DataFrame
    fold_profile: pd.DataFrame


@dataclass(frozen=True)
class ModelComparisonSpec:
    candidate: str
    baseline: str

    @property
    def comparison_id(self) -> str:
        return f"{self.candidate}_minus_{self.baseline}"


def _safe_corr(left: pd.Series, right: pd.Series, *, method: str = "pearson") -> float:
    paired = pd.concat([left, right], axis=1).dropna()
    if len(paired) < 2 or paired.iloc[:, 0].nunique() < 2 or paired.iloc[:, 1].nunique() < 2:
        return float("nan")
    return float(paired.iloc[:, 0].corr(paired.iloc[:, 1], method=method))


def two_sided_normal_p(t_stat: float) -> float:
    if not np.isfinite(t_stat):
        return float("nan")
    return float(math.erfc(abs(float(t_stat)) / math.sqrt(2.0)))


def benjamini_hochberg(p_values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(p_values, errors="coerce")
    result = pd.Series(float("nan"), index=numeric.index, dtype=float)
    valid = numeric.dropna().clip(lower=0.0, upper=1.0)
    if valid.empty:
        return result
    ordered = valid.sort_values(kind="stable")
    count = len(ordered)
    ranks = np.arange(1, count + 1, dtype=float)
    adjusted = ordered.to_numpy(dtype=float) * count / ranks
    adjusted = np.minimum.accumulate(adjusted[::-1])[::-1]
    result.loc[ordered.index] = np.minimum(adjusted, 1.0)
    return result


def normalize_model_predictions(
    predictions: Mapping[str, pd.DataFrame],
    labels: pd.DataFrame,
    *,
    required_models: Sequence[str] = ("ridge", "lightgbm", "xgboost"),
) -> dict[str, pd.DataFrame]:
    normalized_labels = normalize_oos_labels(labels)
    result: dict[str, pd.DataFrame] = {}
    for model, raw in sorted(predictions.items()):
        frame = raw.copy().sort_index()
        if not isinstance(frame.index, pd.MultiIndex) or frame.index.names != [
            "datetime",
            "instrument",
        ]:
            raise ValueError(f"{model} predictions require a datetime/instrument MultiIndex")
        if frame.index.has_duplicates or "score" not in frame:
            raise ValueError(f"{model} predictions have duplicate keys or no score column")
        if not frame.index.equals(normalized_labels.index):
            raise ValueError(f"{model} predictions do not match rolling OOS labels")
        if "label" in frame:
            pd.testing.assert_series_equal(
                pd.to_numeric(frame["label"], errors="coerce"),
                normalized_labels["label"],
                check_dtype=False,
                check_names=False,
            )
        result[model] = pd.DataFrame(
            {
                "score": pd.to_numeric(frame["score"], errors="coerce"),
                "label": normalized_labels["label"],
            },
            index=normalized_labels.index,
        )
    required = tuple(str(value) for value in required_models)
    if not required or len(required) != len(set(required)):
        raise ValueError("required_models must be non-empty and unique")
    if set(result) != set(required):
        raise ValueError(f"regime diagnosis requires exactly these models: {list(required)}")
    return result


def build_oriented_composites(
    features: pd.DataFrame,
    taxonomy: FactorTaxonomy,
    spec: RegimeSpec,
) -> pd.DataFrame:
    if not isinstance(features.index, pd.MultiIndex) or features.index.names != [
        "datetime",
        "instrument",
    ]:
        raise ValueError("composite features require a datetime/instrument MultiIndex")
    missing = sorted(set(value for group in spec.composites.values() for value in group) - set(features))
    if missing:
        raise ValueError(f"composite features are missing: {missing}")
    output: dict[str, pd.Series] = {}
    for name, members in sorted(spec.composites.items()):
        ranked: list[pd.Series] = []
        for feature in members:
            orientation = taxonomy.entry(feature).orientation
            if orientation is None:
                raise ValueError(f"composite feature has unknown direction: {feature}")
            oriented = pd.to_numeric(features[feature], errors="coerce") * orientation
            ranked.append(oriented.groupby(level="datetime", sort=True).rank(method="average", pct=True))
        output[name] = pd.concat(ranked, axis=1).mean(axis=1, skipna=False)
    return pd.DataFrame(output, index=features.index).sort_index()


def derive_model_daily_metrics(
    predictions: Mapping[str, pd.DataFrame],
    *,
    minimum_cross_section: int,
    model_comparisons: Sequence[ModelComparisonSpec] = (
        ModelComparisonSpec("xgboost", "lightgbm"),
        ModelComparisonSpec("xgboost", "ridge"),
    ),
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    by_model: dict[str, pd.DataFrame] = {}
    for model, frame in sorted(predictions.items()):
        model_rows: list[dict[str, object]] = []
        for date, block in frame.groupby(level="datetime", sort=True):
            day = block.droplevel("datetime")[["score", "label"]].dropna()
            eligible = len(day) >= minimum_cross_section
            model_rows.append(
                {
                    "date": pd.Timestamp(date).normalize(),
                    "model": model,
                    "valid_count": len(day),
                    "ic": _safe_corr(day["score"], day["label"]) if eligible else float("nan"),
                    "rank_ic": (
                        _safe_corr(day["score"], day["label"], method="spearman")
                        if eligible
                        else float("nan")
                    ),
                }
            )
        by_model[model] = pd.DataFrame(model_rows).set_index("date")
        rows.extend(model_rows)
    for comparison in model_comparisons:
        missing = {comparison.candidate, comparison.baseline} - set(by_model)
        if missing:
            raise ValueError(
                f"model comparison {comparison.comparison_id} references missing models: {sorted(missing)}"
            )
        left = by_model[comparison.candidate]
        right = by_model[comparison.baseline]
        if not left.index.equals(right.index):
            raise ValueError(f"{comparison.candidate} and {comparison.baseline} daily metrics do not align")
        delta = pd.DataFrame(
            {
                "date": left.index,
                "model": comparison.comparison_id,
                "valid_count": np.minimum(left["valid_count"], right["valid_count"]),
                "ic": left["ic"] - right["ic"],
                "rank_ic": left["rank_ic"] - right["rank_ic"],
            }
        )
        rows.extend(delta.to_dict("records"))
    return pd.DataFrame(rows).sort_values(["date", "model"], kind="stable").reset_index(drop=True)


def _available_regimes(labels: pd.DataFrame) -> pd.DataFrame:
    required = {"date", "dimension", "state", "status"}
    if not required.issubset(labels):
        raise ValueError(f"regime labels are missing columns: {sorted(required - set(labels))}")
    return labels.loc[labels["status"].eq("AVAILABLE"), ["date", "dimension", "state"]]


def derive_factor_regime_diagnostics(
    feature_daily: pd.DataFrame,
    labels: pd.DataFrame,
    taxonomy: FactorTaxonomy,
    spec: RegimeSpec,
    fold_assignments: Mapping[pd.Timestamp, str],
) -> pd.DataFrame:
    selected = feature_daily.loc[feature_daily["feature"].isin(spec.diagnostic_features)].copy()
    if set(selected["feature"].unique()) != set(spec.diagnostic_features):
        missing = sorted(set(spec.diagnostic_features) - set(selected["feature"].unique()))
        raise ValueError(f"feature daily diagnostics are missing candidates: {missing}")
    selected["orientation"] = selected["feature"].map(
        {feature: taxonomy.entry(feature).orientation for feature in spec.diagnostic_features}
    )
    regimes = _available_regimes(labels)
    merged = selected.merge(regimes, on="date", how="inner", validate="many_to_many")
    merged["fold"] = merged["date"].map(fold_assignments)
    if merged["fold"].isna().any():
        raise ValueError("factor regime dates are absent from certified fold assignments")
    rows: list[dict[str, object]] = []
    for (feature, dimension, state), block in merged.groupby(["feature", "dimension", "state"], sort=True):
        orientation = taxonomy.entry(str(feature)).orientation
        sessions = int(block["date"].nunique())
        rank_ic = pd.to_numeric(block["rank_ic"], errors="coerce")
        oriented = rank_ic * orientation if orientation is not None else pd.Series(dtype=float)
        fold_rank_ic = block.assign(_rank_ic=rank_ic).groupby("fold")["_rank_ic"].mean()
        oriented_fold_rank_ic = (
            fold_rank_ic * orientation if orientation is not None else pd.Series(dtype=float)
        )
        sample_status = "SUFFICIENT" if sessions >= spec.minimum_sessions else "INSUFFICIENT_SAMPLE"
        raw_t = newey_west_t(rank_ic, lag=spec.hac_lag)
        oriented_t = newey_west_t(oriented, lag=spec.hac_lag) if orientation is not None else float("nan")
        rows.append(
            {
                "feature": feature,
                "candidate_status": (
                    "STABLE_CANDIDATE" if feature in spec.stable_features else "HYPOTHESIS_ONLY"
                ),
                "direction": taxonomy.entry(str(feature)).direction,
                "dimension": dimension,
                "state": state,
                "sessions": sessions,
                "valid_rank_ic_days": int(rank_ic.notna().sum()),
                "sample_status": sample_status,
                "rank_ic_mean": float(rank_ic.mean()),
                "rank_icir": float(rank_ic.mean() / rank_ic.std(ddof=1))
                if rank_ic.std(ddof=1) > 0
                else float("nan"),
                "rank_ic_hac_t": raw_t,
                "rank_ic_p_value": (
                    two_sided_normal_p(raw_t) if sample_status == "SUFFICIENT" else float("nan")
                ),
                "positive_rank_ic_ratio": float(rank_ic.dropna().gt(0).mean()),
                "valid_folds": int(fold_rank_ic.notna().sum()),
                "positive_rank_ic_fold_ratio": float(fold_rank_ic.dropna().gt(0).mean()),
                "oriented_rank_ic_mean": (
                    float(oriented.mean()) if orientation is not None else float("nan")
                ),
                "oriented_rank_icir": (
                    float(oriented.mean() / oriented.std(ddof=1))
                    if orientation is not None and oriented.std(ddof=1) > 0
                    else float("nan")
                ),
                "oriented_rank_ic_hac_t": oriented_t,
                "positive_oriented_rank_ic_ratio": (
                    float(oriented.dropna().gt(0).mean()) if orientation is not None else float("nan")
                ),
                "positive_oriented_rank_ic_fold_ratio": (
                    float(oriented_fold_rank_ic.dropna().gt(0).mean())
                    if orientation is not None
                    else float("nan")
                ),
            }
        )
    result = pd.DataFrame(rows)
    result["rank_ic_bh_q_value"] = benjamini_hochberg(result["rank_ic_p_value"])
    result["fdr_significant"] = result["rank_ic_bh_q_value"].le(spec.fdr_alpha).fillna(False)
    return result.sort_values(["dimension", "state", "feature"], kind="stable").reset_index(drop=True)


def derive_model_regime_diagnostics(
    model_daily: pd.DataFrame,
    labels: pd.DataFrame,
    spec: RegimeSpec,
    fold_assignments: Mapping[pd.Timestamp, str],
) -> pd.DataFrame:
    merged = model_daily.merge(_available_regimes(labels), on="date", how="inner", validate="many_to_many")
    merged["fold"] = merged["date"].map(fold_assignments)
    if merged["fold"].isna().any():
        raise ValueError("model regime dates are absent from certified fold assignments")
    rows: list[dict[str, object]] = []
    for (model, dimension, state), block in merged.groupby(["model", "dimension", "state"], sort=True):
        sessions = int(block["date"].nunique())
        rank_ic = pd.to_numeric(block["rank_ic"], errors="coerce")
        fold_rank_ic = block.assign(_rank_ic=rank_ic).groupby("fold")["_rank_ic"].mean()
        sample_status = "SUFFICIENT" if sessions >= spec.minimum_sessions else "INSUFFICIENT_SAMPLE"
        rank_t = newey_west_t(rank_ic, lag=spec.hac_lag)
        rows.append(
            {
                "model": model,
                "dimension": dimension,
                "state": state,
                "sessions": sessions,
                "valid_rank_ic_days": int(rank_ic.notna().sum()),
                "sample_status": sample_status,
                "ic_mean": float(pd.to_numeric(block["ic"], errors="coerce").mean()),
                "rank_ic_mean": float(rank_ic.mean()),
                "rank_icir": float(rank_ic.mean() / rank_ic.std(ddof=1))
                if rank_ic.std(ddof=1) > 0
                else float("nan"),
                "rank_ic_hac_t": rank_t,
                "rank_ic_p_value": (
                    two_sided_normal_p(rank_t) if sample_status == "SUFFICIENT" else float("nan")
                ),
                "positive_rank_ic_ratio": float(rank_ic.dropna().gt(0).mean()),
                "valid_folds": int(fold_rank_ic.notna().sum()),
                "positive_rank_ic_fold_ratio": float(fold_rank_ic.dropna().gt(0).mean()),
            }
        )
    result = pd.DataFrame(rows)
    result["rank_ic_bh_q_value"] = benjamini_hochberg(result["rank_ic_p_value"])
    result["fdr_significant"] = result["rank_ic_bh_q_value"].le(spec.fdr_alpha).fillna(False)
    return result.sort_values(["dimension", "state", "model"], kind="stable").reset_index(drop=True)


def _daily_model_composite_correlation(
    predictions: Mapping[str, pd.DataFrame], composites: pd.DataFrame
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for model, frame in sorted(predictions.items()):
        aligned = frame[["score"]].join(composites, how="inner")
        for date, block in aligned.groupby(level="datetime", sort=True):
            day = block.droplevel("datetime")
            for composite in composites.columns:
                rows.append(
                    {
                        "date": pd.Timestamp(date).normalize(),
                        "model": model,
                        "composite": composite,
                        "rank_correlation": _safe_corr(day["score"], day[composite], method="spearman"),
                    }
                )
    return pd.DataFrame(rows)


def derive_model_factor_regime_correlation(
    predictions: Mapping[str, pd.DataFrame],
    composites: pd.DataFrame,
    labels: pd.DataFrame,
    spec: RegimeSpec,
) -> pd.DataFrame:
    daily = _daily_model_composite_correlation(predictions, composites)
    merged = daily.merge(_available_regimes(labels), on="date", how="inner", validate="many_to_many")
    rows: list[dict[str, object]] = []
    for (model, composite, dimension, state), block in merged.groupby(
        ["model", "composite", "dimension", "state"], sort=True
    ):
        sessions = int(block["date"].nunique())
        values = pd.to_numeric(block["rank_correlation"], errors="coerce")
        status = "SUFFICIENT" if sessions >= spec.minimum_sessions else "INSUFFICIENT_SAMPLE"
        t_stat = newey_west_t(values, lag=spec.hac_lag)
        rows.append(
            {
                "model": model,
                "composite": composite,
                "dimension": dimension,
                "state": state,
                "sessions": sessions,
                "sample_status": status,
                "rank_correlation_mean": float(values.mean()),
                "rank_correlation_hac_t": t_stat,
                "p_value": two_sided_normal_p(t_stat) if status == "SUFFICIENT" else float("nan"),
            }
        )
    result = pd.DataFrame(rows)
    result["bh_q_value"] = benjamini_hochberg(result["p_value"])
    return result.sort_values(["dimension", "state", "model", "composite"], kind="stable").reset_index(
        drop=True
    )


def _top_members(values: pd.Series, topk: int) -> set[str]:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if len(clean) < topk:
        return set()
    return {str(value) for value in clean.nlargest(topk, keep="first").index}


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else float("nan")


def derive_topk_regime_overlap(
    predictions: Mapping[str, pd.DataFrame],
    composites: pd.DataFrame,
    labels: pd.DataFrame,
    spec: RegimeSpec,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for model, prediction in sorted(predictions.items()):
        aligned = prediction[["score"]].join(composites, how="inner")
        for date, block in aligned.groupby(level="datetime", sort=True):
            day = block.droplevel("datetime")
            model_top = _top_members(day["score"], spec.topk)
            for composite in composites.columns:
                rows.append(
                    {
                        "date": pd.Timestamp(date).normalize(),
                        "model": model,
                        "composite": composite,
                        "topk": spec.topk,
                        "jaccard": _jaccard(model_top, _top_members(day[composite], spec.topk)),
                    }
                )
    daily = pd.DataFrame(rows)
    merged = daily.merge(_available_regimes(labels), on="date", how="inner", validate="many_to_many")
    result = (
        merged.groupby(["model", "composite", "dimension", "state", "topk"], sort=True)
        .agg(
            sessions=("date", "nunique"),
            jaccard_mean=("jaccard", "mean"),
            jaccard_median=("jaccard", "median"),
        )
        .reset_index()
    )
    result["sample_status"] = np.where(
        result["sessions"].ge(spec.minimum_sessions), "SUFFICIENT", "INSUFFICIENT_SAMPLE"
    )
    return result.sort_values(["dimension", "state", "model", "composite"], kind="stable").reset_index(
        drop=True
    )


def derive_fold_regime_profile(
    labels: pd.DataFrame,
    fold_assignments: Mapping[pd.Timestamp, str],
) -> pd.DataFrame:
    available = _available_regimes(labels).copy()
    available["fold"] = available["date"].map(fold_assignments)
    if available["fold"].isna().any():
        raise ValueError("regime dates are absent from certified fold assignments")
    counts = (
        available.groupby(["fold", "dimension", "state"], sort=True)
        .agg(sessions=("date", "nunique"))
        .reset_index()
    )
    totals = counts.groupby(["fold", "dimension"])["sessions"].transform("sum")
    counts["session_ratio"] = counts["sessions"] / totals
    counts["dominant_state"] = counts["sessions"].eq(
        counts.groupby(["fold", "dimension"])["sessions"].transform("max")
    )
    return counts.sort_values(["fold", "dimension", "state"], kind="stable").reset_index(drop=True)


def build_regime_diagnostics(
    *,
    regime_labels: pd.DataFrame,
    feature_daily: pd.DataFrame,
    features: pd.DataFrame,
    labels: pd.DataFrame,
    predictions: Mapping[str, pd.DataFrame],
    taxonomy: FactorTaxonomy,
    spec: RegimeSpec,
    fold_assignments: Mapping[pd.Timestamp, str],
    minimum_cross_section: int = 50,
    required_models: Sequence[str] = ("ridge", "lightgbm", "xgboost"),
    model_comparisons: Sequence[ModelComparisonSpec] = (
        ModelComparisonSpec("xgboost", "lightgbm"),
        ModelComparisonSpec("xgboost", "ridge"),
    ),
) -> RegimeDiagnosticArtifacts:
    normalized_predictions = normalize_model_predictions(predictions, labels, required_models=required_models)
    composites = build_oriented_composites(features, taxonomy, spec)
    model_daily = derive_model_daily_metrics(
        normalized_predictions,
        minimum_cross_section=minimum_cross_section,
        model_comparisons=model_comparisons,
    )
    return RegimeDiagnosticArtifacts(
        labels=regime_labels,
        factor_regime=derive_factor_regime_diagnostics(
            feature_daily, regime_labels, taxonomy, spec, fold_assignments
        ),
        model_regime=derive_model_regime_diagnostics(model_daily, regime_labels, spec, fold_assignments),
        model_factor_correlation=derive_model_factor_regime_correlation(
            normalized_predictions, composites, regime_labels, spec
        ),
        topk_overlap=derive_topk_regime_overlap(normalized_predictions, composites, regime_labels, spec),
        fold_profile=derive_fold_regime_profile(regime_labels, fold_assignments),
    )
