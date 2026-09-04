from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
import yaml

from qlib_platform.lineage import sha256_json
from qlib_platform.data.store import sha256_file
from qlib_platform.research.factor_taxonomy import FactorTaxonomy


EXPLANATION_SCHEMA = "ashare_model_explanation_v1"
HYPOTHESIS_STATUSES = {"SUPPORTED", "REJECTED", "INCONCLUSIVE"}
MECHANISMS = {
    "MAIN_EFFECT_NONLINEAR",
    "PERSISTENT_INTERACTIONS",
    "FOLD_UNSTABLE",
    "MIXED",
    "INCONCLUSIVE",
}


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return value


def _positive_int(value: object, name: str) -> int:
    parsed = int(str(value))
    if parsed < 1:
        raise ValueError(f"{name} must be positive")
    return parsed


def _unit_interval(value: object, name: str) -> float:
    parsed = float(str(value))
    if not 0 <= parsed <= 1:
        raise ValueError(f"{name} must be in [0, 1]")
    return parsed


@dataclass(frozen=True)
class ModelExplanationSpec:
    explanation_id: str
    top_features: int
    minimum_cross_section: int
    minimum_regime_sessions: int
    interaction_rows_per_fold: int
    interaction_top_pairs: int
    random_seed: int
    score_parity_tolerance: float
    shap_additivity_tolerance: float
    h1_main_effect_families: tuple[str, ...]
    h1_minimum_shap_share: float
    h2_interaction_family_pairs: tuple[tuple[str, str], ...]
    h2_minimum_interaction_share: float
    h2_minimum_fold_persistence: float
    h3_minimum_top_feature_jaccard: float
    h3_minimum_direction_agreement: float
    h3_maximum_magnitude_cv: float
    h3_maximum_regime_drift: float
    semantic_sha256: str
    file_sha256: str

    def to_manifest(self) -> dict[str, object]:
        return {
            "schema": EXPLANATION_SCHEMA,
            "explanationId": self.explanation_id,
            "topFeatures": self.top_features,
            "minimumCrossSection": self.minimum_cross_section,
            "minimumRegimeSessions": self.minimum_regime_sessions,
            "interactionRowsPerFold": self.interaction_rows_per_fold,
            "interactionTopPairs": self.interaction_top_pairs,
            "randomSeed": self.random_seed,
            "scoreParityTolerance": self.score_parity_tolerance,
            "shapAdditivityTolerance": self.shap_additivity_tolerance,
            "hypotheses": {
                "h1MainEffectFamilies": list(self.h1_main_effect_families),
                "h1MinimumShapShare": self.h1_minimum_shap_share,
                "h2InteractionFamilyPairs": [list(pair) for pair in self.h2_interaction_family_pairs],
                "h2MinimumInteractionShare": self.h2_minimum_interaction_share,
                "h2MinimumFoldPersistence": self.h2_minimum_fold_persistence,
                "h3MinimumTopFeatureJaccard": self.h3_minimum_top_feature_jaccard,
                "h3MinimumDirectionAgreement": self.h3_minimum_direction_agreement,
                "h3MaximumMagnitudeCv": self.h3_maximum_magnitude_cv,
                "h3MaximumRegimeDrift": self.h3_maximum_regime_drift,
            },
            "semanticSha256": self.semantic_sha256,
            "fileSha256": self.file_sha256,
        }


def load_model_explanation_spec(path: str | Path) -> ModelExplanationSpec:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"model explanation config is missing: {source}")
    raw = _mapping(yaml.safe_load(source.read_text(encoding="utf-8")), "model explanation config")
    if raw.get("schema") != EXPLANATION_SCHEMA:
        raise ValueError(f"unsupported model explanation schema: {raw.get('schema')}")
    explanation_id = str(raw.get("explanationId") or "").strip()
    if not explanation_id:
        raise ValueError("explanationId is required")
    hypotheses = _mapping(raw.get("hypotheses"), "hypotheses")
    families = tuple(str(value) for value in hypotheses.get("h1MainEffectFamilies", ()))
    if families != ("Value", "Volatility"):
        raise ValueError("H1 main-effect families must remain Value and Volatility")
    raw_pairs = hypotheses.get("h2InteractionFamilyPairs")
    if not isinstance(raw_pairs, list) or not raw_pairs:
        raise ValueError("H2 interaction family pairs must be a non-empty list")
    pairs: list[tuple[str, str]] = []
    for raw_pair in raw_pairs:
        if not isinstance(raw_pair, list) or len(raw_pair) != 2:
            raise ValueError("each H2 interaction family pair must contain two families")
        pair = tuple(sorted((str(raw_pair[0]), str(raw_pair[1]))))
        pairs.append((pair[0], pair[1]))
    expected_pairs = {
        tuple(sorted(pair))
        for pair in (("Value", "Volatility"), ("Value", "Size"), ("Volatility", "Momentum"))
    }
    if set(pairs) != expected_pairs:
        raise ValueError("H2 family pairs must remain Value×Volatility, Value×Size, Volatility×Momentum")
    top_features = _positive_int(raw.get("topFeatures"), "topFeatures")
    minimum_cross_section = _positive_int(raw.get("minimumCrossSection"), "minimumCrossSection")
    minimum_regime_sessions = _positive_int(raw.get("minimumRegimeSessions"), "minimumRegimeSessions")
    interaction_rows_per_fold = _positive_int(raw.get("interactionRowsPerFold"), "interactionRowsPerFold")
    interaction_top_pairs = _positive_int(raw.get("interactionTopPairs"), "interactionTopPairs")
    random_seed = int(str(raw.get("randomSeed")))
    score_parity_tolerance = float(str(raw.get("scoreParityTolerance")))
    shap_additivity_tolerance = float(str(raw.get("shapAdditivityTolerance")))
    h1_minimum_shap_share = _unit_interval(hypotheses.get("h1MinimumShapShare"), "h1MinimumShapShare")
    h2_minimum_interaction_share = _unit_interval(
        hypotheses.get("h2MinimumInteractionShare"), "h2MinimumInteractionShare"
    )
    h2_minimum_fold_persistence = _unit_interval(
        hypotheses.get("h2MinimumFoldPersistence"), "h2MinimumFoldPersistence"
    )
    h3_minimum_top_feature_jaccard = _unit_interval(
        hypotheses.get("h3MinimumTopFeatureJaccard"), "h3MinimumTopFeatureJaccard"
    )
    h3_minimum_direction_agreement = _unit_interval(
        hypotheses.get("h3MinimumDirectionAgreement"), "h3MinimumDirectionAgreement"
    )
    h3_maximum_magnitude_cv = float(str(hypotheses.get("h3MaximumMagnitudeCv")))
    h3_maximum_regime_drift = _unit_interval(hypotheses.get("h3MaximumRegimeDrift"), "h3MaximumRegimeDrift")
    semantic: dict[str, object] = {
        "schema": EXPLANATION_SCHEMA,
        "explanationId": explanation_id,
        "topFeatures": top_features,
        "minimumCrossSection": minimum_cross_section,
        "minimumRegimeSessions": minimum_regime_sessions,
        "interactionRowsPerFold": interaction_rows_per_fold,
        "interactionTopPairs": interaction_top_pairs,
        "randomSeed": random_seed,
        "scoreParityTolerance": score_parity_tolerance,
        "shapAdditivityTolerance": shap_additivity_tolerance,
        "hypotheses": {
            "h1MainEffectFamilies": list(families),
            "h1MinimumShapShare": h1_minimum_shap_share,
            "h2InteractionFamilyPairs": [list(pair) for pair in pairs],
            "h2MinimumInteractionShare": h2_minimum_interaction_share,
            "h2MinimumFoldPersistence": h2_minimum_fold_persistence,
            "h3MinimumTopFeatureJaccard": h3_minimum_top_feature_jaccard,
            "h3MinimumDirectionAgreement": h3_minimum_direction_agreement,
            "h3MaximumMagnitudeCv": h3_maximum_magnitude_cv,
            "h3MaximumRegimeDrift": h3_maximum_regime_drift,
        },
    }
    if top_features > interaction_rows_per_fold:
        raise ValueError("interactionRowsPerFold must be at least topFeatures")
    if score_parity_tolerance <= 0 or shap_additivity_tolerance <= 0:
        raise ValueError("model explanation tolerances must be positive")
    if h3_maximum_magnitude_cv <= 0:
        raise ValueError("h3MaximumMagnitudeCv must be positive")
    return ModelExplanationSpec(
        explanation_id=explanation_id,
        top_features=top_features,
        minimum_cross_section=minimum_cross_section,
        minimum_regime_sessions=minimum_regime_sessions,
        interaction_rows_per_fold=interaction_rows_per_fold,
        interaction_top_pairs=interaction_top_pairs,
        random_seed=random_seed,
        score_parity_tolerance=score_parity_tolerance,
        shap_additivity_tolerance=shap_additivity_tolerance,
        h1_main_effect_families=families,
        h1_minimum_shap_share=h1_minimum_shap_share,
        h2_interaction_family_pairs=tuple(pairs),
        h2_minimum_interaction_share=h2_minimum_interaction_share,
        h2_minimum_fold_persistence=h2_minimum_fold_persistence,
        h3_minimum_top_feature_jaccard=h3_minimum_top_feature_jaccard,
        h3_minimum_direction_agreement=h3_minimum_direction_agreement,
        h3_maximum_magnitude_cv=h3_maximum_magnitude_cv,
        h3_maximum_regime_drift=h3_maximum_regime_drift,
        semantic_sha256=sha256_json(semantic),
        file_sha256=sha256_file(source),
    )


def _feature_fields(taxonomy: FactorTaxonomy, feature: str) -> dict[str, object]:
    entry = taxonomy.entry(feature)
    return {"family": entry.family, "role": entry.role, "direction": entry.direction}


def _safe_spearman(left: np.ndarray, right: np.ndarray) -> float:
    frame = pd.DataFrame({"left": left, "right": right}).replace([np.inf, -np.inf], np.nan).dropna()
    if len(frame) < 2 or frame["left"].nunique() < 2 or frame["right"].nunique() < 2:
        return float("nan")
    return float(frame["left"].corr(frame["right"], method="spearman"))


def _rank_normalized(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    finite = np.where(np.isfinite(values), np.maximum(values, 0.0), 0.0)
    total = float(finite.sum())
    normalized = finite / total if total > 0 else np.zeros(len(finite), dtype=float)
    ranks = pd.Series(normalized).rank(method="first", ascending=False).to_numpy(dtype=int)
    return normalized, ranks


def derive_tree_importance(
    booster: Any,
    *,
    model: str,
    fold: str,
    feature_names: Sequence[str],
    taxonomy: FactorTaxonomy,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    if model == "xgboost":
        for importance_type, native in (("gain", "gain"), ("split", "weight")):
            raw = booster.get_score(importance_type=native)
            values = np.zeros(len(feature_names), dtype=float)
            for key, value in raw.items():
                name = str(key)
                if name in feature_names:
                    position = list(feature_names).index(name)
                elif name.startswith("f") and name[1:].isdigit():
                    position = int(name[1:])
                else:
                    raise ValueError(f"unrecognized XGBoost feature name: {name}")
                if position >= len(values):
                    raise ValueError(f"XGBoost feature position is outside the feature contract: {position}")
                values[position] = float(value)
            normalized, ranks = _rank_normalized(values)
            for position, feature in enumerate(feature_names):
                rows.append(
                    {
                        "model": model,
                        "scope_type": "FOLD",
                        "scope": fold,
                        "fold": fold,
                        "feature": feature,
                        **_feature_fields(taxonomy, feature),
                        "importance_type": importance_type,
                        "raw_importance": values[position],
                        "normalized_importance": normalized[position],
                        "rank": ranks[position],
                        "sample_status": "AVAILABLE",
                    }
                )
    elif model == "lightgbm":
        if int(booster.num_feature()) != len(feature_names):
            raise ValueError("LightGBM feature count differs from the feature contract")
        for importance_type in ("gain", "split"):
            values = np.asarray(booster.feature_importance(importance_type=importance_type), dtype=float)
            normalized, ranks = _rank_normalized(values)
            for position, feature in enumerate(feature_names):
                rows.append(
                    {
                        "model": model,
                        "scope_type": "FOLD",
                        "scope": fold,
                        "fold": fold,
                        "feature": feature,
                        **_feature_fields(taxonomy, feature),
                        "importance_type": importance_type,
                        "raw_importance": values[position],
                        "normalized_importance": normalized[position],
                        "rank": ranks[position],
                        "sample_status": "AVAILABLE",
                    }
                )
    else:
        raise ValueError("tree importance supports only xgboost and lightgbm")
    return pd.DataFrame(rows)


def derive_ridge_importance(
    coefficients: np.ndarray,
    *,
    fold: str,
    feature_names: Sequence[str],
    taxonomy: FactorTaxonomy,
) -> pd.DataFrame:
    values = np.asarray(coefficients, dtype=float).reshape(-1)
    if len(values) != len(feature_names):
        raise ValueError("Ridge coefficient width differs from the feature contract")
    normalized, ranks = _rank_normalized(np.abs(values))
    return pd.DataFrame(
        [
            {
                "model": "ridge",
                "scope_type": "FOLD",
                "scope": fold,
                "fold": fold,
                "feature": feature,
                **_feature_fields(taxonomy, feature),
                "importance_type": "coefficient",
                "raw_importance": values[position],
                "normalized_importance": normalized[position],
                "rank": ranks[position],
                "sample_status": "AVAILABLE",
            }
            for position, feature in enumerate(feature_names)
        ]
    )


def shap_summary_rows(
    features: pd.DataFrame,
    shap_values: np.ndarray,
    *,
    model: str,
    scope_type: str,
    scope: str,
    fold: str | None,
    taxonomy: FactorTaxonomy,
    additivity_max_abs_error: float,
    sample_status: str = "AVAILABLE",
    dimension: str | None = None,
    state: str | None = None,
) -> list[dict[str, object]]:
    values = np.asarray(shap_values, dtype=float)
    if values.shape != features.shape:
        raise ValueError("SHAP contribution matrix differs from the feature matrix")
    mean_abs = np.nanmean(np.abs(values), axis=0) if len(features) else np.zeros(features.shape[1])
    normalized, ranks = _rank_normalized(mean_abs)
    rows: list[dict[str, object]] = []
    sessions = int(features.index.get_level_values("datetime").nunique()) if len(features) else 0
    for position, feature in enumerate(features.columns):
        column = values[:, position]
        row: dict[str, object] = {
            "model": model,
            "scope_type": scope_type,
            "scope": scope,
            "fold": fold,
            "feature": str(feature),
            **_feature_fields(taxonomy, str(feature)),
            "observations": len(features),
            "sessions": sessions,
            "mean_shap": float(np.nanmean(column)) if len(column) else float("nan"),
            "mean_abs_shap": float(mean_abs[position]),
            "normalized_mean_abs_shap": float(normalized[position]),
            "shap_std": float(np.nanstd(column, ddof=1)) if len(column) > 1 else float("nan"),
            "feature_shap_spearman": _safe_spearman(features.iloc[:, position].to_numpy(dtype=float), column),
            "rank": int(ranks[position]),
            "additivity_max_abs_error": additivity_max_abs_error,
            "sample_status": sample_status,
        }
        if dimension is not None:
            row["dimension"] = dimension
        if state is not None:
            row["state"] = state
        rows.append(row)
    return rows


def deterministic_sample_positions(index: pd.Index, *, count: int, seed: int, namespace: str) -> np.ndarray:
    if count >= len(index):
        return np.arange(len(index), dtype=int)
    hashes = pd.util.hash_pandas_object(index, index=True).to_numpy(dtype=np.uint64)
    salt = int(sha256_json({"seed": seed, "namespace": namespace})[:16], 16)
    order = np.argsort(hashes ^ np.uint64(salt), kind="stable")
    return np.sort(order[:count])


def derive_xgb_interactions(
    interaction_values: np.ndarray,
    *,
    fold: str,
    feature_names: Sequence[str],
    taxonomy: FactorTaxonomy,
    top_pairs: int,
    observations: int,
    sessions: int,
) -> pd.DataFrame:
    values = np.asarray(interaction_values, dtype=float)
    width = len(feature_names)
    if values.shape != (observations, width, width):
        raise ValueError("XGBoost interaction matrix differs from the explanation contract")
    rows: list[dict[str, object]] = []
    pair_values: list[tuple[int, int, float]] = []
    for left, right in combinations(range(width), 2):
        mean_abs = float(np.mean(2.0 * np.abs(values[:, left, right])))
        pair_values.append((left, right, mean_abs))
    pair_values.sort(key=lambda item: (-item[2], feature_names[item[0]], feature_names[item[1]]))
    selected = pair_values[: min(top_pairs, len(pair_values))]
    denominator = sum(value for _, _, value in selected)
    for rank, (left, right, value) in enumerate(selected, start=1):
        feature_1, feature_2 = str(feature_names[left]), str(feature_names[right])
        rows.append(
            {
                "scope_type": "FOLD",
                "scope": fold,
                "fold": fold,
                "feature_1": feature_1,
                "feature_2": feature_2,
                "family_1": taxonomy.entry(feature_1).family,
                "family_2": taxonomy.entry(feature_2).family,
                "observations": observations,
                "sessions": sessions,
                "mean_abs_pair_interaction": value,
                "normalized_share": value / denominator if denominator > 0 else 0.0,
                "rank": rank,
                "sample_status": "AVAILABLE",
            }
        )
    return pd.DataFrame(rows)


def _pairwise_jaccard(groups: Mapping[str, set[str]]) -> float:
    values: list[float] = []
    for left, right in combinations(sorted(groups), 2):
        union = groups[left] | groups[right]
        values.append(len(groups[left] & groups[right]) / len(union) if union else 1.0)
    return float(np.mean(values)) if values else float("nan")


def derive_explanation_stability(
    shap_by_fold: pd.DataFrame,
    shap_by_regime: pd.DataFrame,
    interactions: pd.DataFrame,
    *,
    spec: ModelExplanationSpec,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for model, block in shap_by_fold.groupby("model", sort=True):
        top_sets = {
            str(fold): set(group.nsmallest(spec.top_features, "rank")["feature"].astype(str))
            for fold, group in block.groupby("fold", sort=True)
        }
        jaccard = _pairwise_jaccard(top_sets)
        rows.append(
            {
                "entity_type": "MODEL",
                "entity": model,
                "metric": "top_feature_jaccard",
                "value": jaccard,
                "count": len(top_sets),
                "status": (
                    "STABLE"
                    if np.isfinite(jaccard) and jaccard >= spec.h3_minimum_top_feature_jaccard
                    else "UNSTABLE"
                ),
            }
        )
        directions: list[float] = []
        magnitude_cvs: list[float] = []
        for _, feature_rows in block.groupby("feature", sort=True):
            correlations = pd.to_numeric(feature_rows["feature_shap_spearman"], errors="coerce").dropna()
            nonzero = correlations.loc[correlations.ne(0)]
            if len(nonzero) >= 2:
                directions.append(float(max(nonzero.gt(0).mean(), nonzero.lt(0).mean())))
            magnitudes = pd.to_numeric(feature_rows["normalized_mean_abs_shap"], errors="coerce").dropna()
            if len(magnitudes) >= 2 and float(magnitudes.mean()) > 0:
                magnitude_cvs.append(float(magnitudes.std(ddof=1) / magnitudes.mean()))
        direction = float(np.mean(directions)) if directions else float("nan")
        magnitude_cv = float(np.median(magnitude_cvs)) if magnitude_cvs else float("nan")
        rows.extend(
            [
                {
                    "entity_type": "MODEL",
                    "entity": model,
                    "metric": "shap_direction_agreement",
                    "value": direction,
                    "count": len(directions),
                    "status": (
                        "STABLE"
                        if np.isfinite(direction) and direction >= spec.h3_minimum_direction_agreement
                        else "UNSTABLE"
                    ),
                },
                {
                    "entity_type": "MODEL",
                    "entity": model,
                    "metric": "normalized_magnitude_cv",
                    "value": magnitude_cv,
                    "count": len(magnitude_cvs),
                    "status": (
                        "STABLE"
                        if np.isfinite(magnitude_cv) and magnitude_cv <= spec.h3_maximum_magnitude_cv
                        else "UNSTABLE"
                    ),
                },
            ]
        )
    if not shap_by_regime.empty:
        sufficient = shap_by_regime.loc[shap_by_regime["sample_status"].eq("SUFFICIENT")]
        for model, block in sufficient.groupby("model", sort=True):
            top_sets = {
                f"{dimension}:{state}": set(group.nsmallest(spec.top_features, "rank")["feature"].astype(str))
                for (dimension, state), group in block.groupby(["dimension", "state"], sort=True)
            }
            jaccard = _pairwise_jaccard(top_sets)
            drift = 1.0 - jaccard if np.isfinite(jaccard) else float("nan")
            rows.append(
                {
                    "entity_type": "MODEL",
                    "entity": model,
                    "metric": "regime_importance_drift",
                    "value": drift,
                    "count": len(top_sets),
                    "status": (
                        "STABLE"
                        if np.isfinite(drift) and drift <= spec.h3_maximum_regime_drift
                        else "UNSTABLE"
                    ),
                }
            )
    if not interactions.empty:
        fold_count = int(interactions["fold"].nunique())
        pairs = interactions.assign(
            pair=interactions["feature_1"].astype(str) + "×" + interactions["feature_2"].astype(str)
        )
        for pair, block in pairs.groupby("pair", sort=True):
            rows.append(
                {
                    "entity_type": "INTERACTION",
                    "entity": pair,
                    "metric": "fold_presence_rate",
                    "value": block["fold"].nunique() / fold_count if fold_count else float("nan"),
                    "count": int(block["fold"].nunique()),
                    "status": (
                        "PERSISTENT"
                        if fold_count
                        and block["fold"].nunique() / fold_count >= spec.h2_minimum_fold_persistence
                        else "UNSTABLE"
                    ),
                }
            )
    return (
        pd.DataFrame(rows)
        .sort_values(["entity_type", "entity", "metric"], kind="stable")
        .reset_index(drop=True)
    )


def _metric(stability: pd.DataFrame, model: str, metric: str) -> float:
    rows = stability.loc[
        stability["entity_type"].eq("MODEL") & stability["entity"].eq(model) & stability["metric"].eq(metric),
        "value",
    ]
    return float(rows.iloc[0]) if len(rows) == 1 else float("nan")


def derive_model_explanation_summary(
    shap_summary: pd.DataFrame,
    interactions: pd.DataFrame,
    stability: pd.DataFrame,
    *,
    spec: ModelExplanationSpec,
    regime_conditioning: str,
) -> dict[str, object]:
    xgb = shap_summary.loc[shap_summary["model"].eq("xgboost")]
    if xgb.empty:
        raise ValueError("XGBoost SHAP summary is required")
    main_share = float(
        xgb.loc[xgb["family"].isin(spec.h1_main_effect_families), "normalized_mean_abs_shap"].sum()
    )
    target_pairs = {tuple(sorted(pair)) for pair in spec.h2_interaction_family_pairs}
    if interactions.empty:
        interaction_share = float("nan")
        persistent_share = float("nan")
    else:
        pair_frame = interactions.copy()
        pair_frame["family_pair"] = [
            tuple(sorted((str(left), str(right))))
            for left, right in zip(pair_frame["family_1"], pair_frame["family_2"], strict=True)
        ]
        target = pair_frame.loc[pair_frame["family_pair"].isin(target_pairs)]
        fold_count = int(pair_frame["fold"].nunique())
        per_fold_share = (
            target.groupby("fold")["normalized_share"]
            .sum()
            .reindex(pair_frame["fold"].drop_duplicates(), fill_value=0.0)
            if fold_count
            else pd.Series(dtype=float)
        )
        interaction_share = float(per_fold_share.mean()) if len(per_fold_share) else 0.0
        presence = (
            target.groupby(["feature_1", "feature_2"])["fold"].nunique() / fold_count
            if fold_count and len(target)
            else pd.Series(dtype=float)
        )
        persistent_share = float(presence.max()) if len(presence) else 0.0
    top_jaccard = _metric(stability, "xgboost", "top_feature_jaccard")
    direction = _metric(stability, "xgboost", "shap_direction_agreement")
    magnitude_cv = _metric(stability, "xgboost", "normalized_magnitude_cv")
    regime_drift = _metric(stability, "xgboost", "regime_importance_drift")
    h1_supported = main_share >= spec.h1_minimum_shap_share
    h2_supported = (
        np.isfinite(interaction_share)
        and interaction_share >= spec.h2_minimum_interaction_share
        and persistent_share >= spec.h2_minimum_fold_persistence
    )
    instability_tests = [
        np.isfinite(top_jaccard) and top_jaccard < spec.h3_minimum_top_feature_jaccard,
        np.isfinite(direction) and direction < spec.h3_minimum_direction_agreement,
        np.isfinite(magnitude_cv) and magnitude_cv > spec.h3_maximum_magnitude_cv,
        np.isfinite(regime_drift) and regime_drift > spec.h3_maximum_regime_drift,
    ]
    h3_supported = any(instability_tests)
    h1_status = "SUPPORTED" if h1_supported else "REJECTED"
    h2_status = "SUPPORTED" if h2_supported else "REJECTED"
    h3_status = "SUPPORTED" if h3_supported else "REJECTED"
    supported = [
        name for name, value in (("H1", h1_supported), ("H2", h2_supported), ("H3", h3_supported)) if value
    ]
    mechanism = (
        "INCONCLUSIVE"
        if not supported
        else "MAIN_EFFECT_NONLINEAR"
        if supported == ["H1"]
        else "PERSISTENT_INTERACTIONS"
        if supported == ["H2"]
        else "FOLD_UNSTABLE"
        if supported == ["H3"]
        else "MIXED"
    )
    if mechanism not in MECHANISMS:
        raise AssertionError("invalid explanation mechanism")
    return {
        "hypotheses": {
            "H1": {
                "status": h1_status,
                "statement": "XGBoost primarily uses Value and Low-Volatility through nonlinear main effects.",
                "valueLowVolShapShare": main_share,
                "threshold": spec.h1_minimum_shap_share,
            },
            "H2": {
                "status": h2_status,
                "statement": "XGBoost relies on persistent Value×Volatility, Value×Size, or Volatility×Momentum interactions.",
                "targetInteractionShare": interaction_share,
                "maximumFoldPersistence": persistent_share,
                "shareThreshold": spec.h2_minimum_interaction_share,
                "persistenceThreshold": spec.h2_minimum_fold_persistence,
            },
            "H3": {
                "status": h3_status,
                "statement": "XGBoost learns unstable relationships across folds or regimes.",
                "topFeatureJaccard": top_jaccard,
                "shapDirectionAgreement": direction,
                "normalizedMagnitudeCv": magnitude_cv,
                "regimeImportanceDrift": regime_drift,
            },
        },
        "xgbPrimaryMechanism": mechanism,
        "stableSignalStructure": bool(not h3_supported and np.isfinite(top_jaccard)),
        "foldRelationshipStability": "UNSTABLE" if h3_supported else "STABLE",
        "regimeImportanceDrift": (
            "INPUT_PARTIAL"
            if regime_conditioning == "PARTIAL"
            else "HIGH"
            if np.isfinite(regime_drift) and regime_drift > spec.h3_maximum_regime_drift
            else "LOW"
            if np.isfinite(regime_drift)
            else "INCONCLUSIVE"
        ),
        "boundedSensitivity": "NOT_RUN_NO_RETRAIN_AUTHORIZED",
        "permutationImportance": "NOT_RUN_NO_PREDICT_AUTHORIZED",
    }
