from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd
import yaml

from ..lineage import sha256_json
from ..store import sha256_file
from .regime_diagnostics import normalize_model_predictions


ATTRIBUTION_SCHEMA = "ashare_failure_attribution_v1"
PRIMARY_LOSS_SOURCES = {"SIGNAL", "MODEL", "RANKING", "PORTFOLIO", "COST", "REGIME", "MIXED"}


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return value


def _positive_int(value: object, name: str) -> int:
    parsed = int(str(value))
    if parsed < 1:
        raise ValueError(f"{name} must be positive")
    return parsed


@dataclass(frozen=True)
class PortfolioVariant:
    name: str
    topk: int
    n_drop: int
    hold_threshold: int

    def to_manifest(self) -> dict[str, object]:
        return {
            "name": self.name,
            "topk": self.topk,
            "nDrop": self.n_drop,
            "holdThreshold": self.hold_threshold,
        }


@dataclass(frozen=True)
class FailureAttributionSpec:
    attribution_id: str
    minimum_cross_section: int
    annualization_sessions: int
    label_horizon_sessions: int
    failure_fold: str
    portfolio_variants: Mapping[str, PortfolioVariant]
    cost_multipliers: tuple[float, ...]
    minimum_rank_ic: float
    cost_primary_gross_fraction: float
    model_topk_near_identity: float
    topk_increment_tolerance: float
    semantic_sha256: str
    file_sha256: str

    @property
    def baseline(self) -> PortfolioVariant:
        return self.portfolio_variants["baseline"]

    def to_manifest(self) -> dict[str, object]:
        return {
            "schema": ATTRIBUTION_SCHEMA,
            "attributionId": self.attribution_id,
            "minimumCrossSection": self.minimum_cross_section,
            "annualizationSessions": self.annualization_sessions,
            "labelHorizonSessions": self.label_horizon_sessions,
            "failureFold": self.failure_fold,
            "portfolioVariants": {
                key: value.to_manifest() for key, value in sorted(self.portfolio_variants.items())
            },
            "costMultipliers": list(self.cost_multipliers),
            "classification": {
                "minimumRankIc": self.minimum_rank_ic,
                "costPrimaryGrossFraction": self.cost_primary_gross_fraction,
                "modelTopkNearIdentity": self.model_topk_near_identity,
                "topkIncrementTolerance": self.topk_increment_tolerance,
            },
            "semanticSha256": self.semantic_sha256,
            "fileSha256": self.file_sha256,
        }


def load_failure_attribution_spec(path: str | Path) -> FailureAttributionSpec:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"attribution config is missing: {source}")
    raw = _mapping(yaml.safe_load(source.read_text(encoding="utf-8")), "attribution config")
    if raw.get("schema") != ATTRIBUTION_SCHEMA:
        raise ValueError(f"unsupported attribution schema: {raw.get('schema')}")
    attribution_id = str(raw.get("attributionId") or "").strip()
    if not attribution_id:
        raise ValueError("attributionId is required")
    variants_raw = _mapping(raw.get("portfolioVariants"), "portfolioVariants")
    if set(variants_raw) != {"baseline", "topk20", "topk50"}:
        raise ValueError("portfolioVariants must be exactly baseline, topk20, and topk50")
    variants: dict[str, PortfolioVariant] = {}
    for name, value in variants_raw.items():
        item = _mapping(value, f"portfolio variant {name}")
        variants[str(name)] = PortfolioVariant(
            name=str(name),
            topk=_positive_int(item.get("topk"), f"{name}.topk"),
            n_drop=_positive_int(item.get("nDrop"), f"{name}.nDrop"),
            hold_threshold=_positive_int(item.get("holdThreshold"), f"{name}.holdThreshold"),
        )
    expected_variants = {
        "baseline": (30, 5, 5),
        "topk20": (20, 5, 5),
        "topk50": (50, 10, 5),
    }
    if any(
        (variants[name].topk, variants[name].n_drop, variants[name].hold_threshold) != expected
        for name, expected in expected_variants.items()
    ):
        raise ValueError("portfolio variants must remain the predeclared 30/20/50 bounded set")
    multipliers = tuple(float(str(value)) for value in raw.get("costMultipliers", ()))
    if multipliers != (0.0, 0.5, 1.0, 1.5, 2.0):
        raise ValueError("costMultipliers must remain [0, 0.5, 1, 1.5, 2]")
    classification = _mapping(raw.get("classification"), "classification")
    values = {
        "minimumRankIc": float(str(classification.get("minimumRankIc"))),
        "costPrimaryGrossFraction": float(str(classification.get("costPrimaryGrossFraction"))),
        "modelTopkNearIdentity": float(str(classification.get("modelTopkNearIdentity"))),
        "topkIncrementTolerance": float(str(classification.get("topkIncrementTolerance"))),
    }
    if not 0 <= values["costPrimaryGrossFraction"] <= 1:
        raise ValueError("costPrimaryGrossFraction must be in [0, 1]")
    if not 0 <= values["modelTopkNearIdentity"] <= 1:
        raise ValueError("modelTopkNearIdentity must be in [0, 1]")
    semantic = {
        "schema": ATTRIBUTION_SCHEMA,
        "attributionId": attribution_id,
        "minimumCrossSection": _positive_int(raw.get("minimumCrossSection"), "minimumCrossSection"),
        "annualizationSessions": _positive_int(raw.get("annualizationSessions"), "annualizationSessions"),
        "labelHorizonSessions": _positive_int(raw.get("labelHorizonSessions"), "labelHorizonSessions"),
        "failureFold": str(raw.get("failureFold") or "").strip(),
        "portfolioVariants": {key: value.to_manifest() for key, value in sorted(variants.items())},
        "costMultipliers": list(multipliers),
        "classification": values,
    }
    if not semantic["failureFold"]:
        raise ValueError("failureFold is required")
    return FailureAttributionSpec(
        attribution_id=attribution_id,
        minimum_cross_section=int(str(semantic["minimumCrossSection"])),
        annualization_sessions=int(str(semantic["annualizationSessions"])),
        label_horizon_sessions=int(str(semantic["labelHorizonSessions"])),
        failure_fold=str(semantic["failureFold"]),
        portfolio_variants=variants,
        cost_multipliers=multipliers,
        minimum_rank_ic=values["minimumRankIc"],
        cost_primary_gross_fraction=values["costPrimaryGrossFraction"],
        model_topk_near_identity=values["modelTopkNearIdentity"],
        topk_increment_tolerance=values["topkIncrementTolerance"],
        semantic_sha256=sha256_json(semantic),
        file_sha256=sha256_file(source),
    )


def _safe_corr(left: pd.Series, right: pd.Series, *, method: str) -> float:
    paired = pd.concat([left, right], axis=1).dropna()
    if len(paired) < 2 or paired.iloc[:, 0].nunique() < 2 or paired.iloc[:, 1].nunique() < 2:
        return float("nan")
    return float(paired.iloc[:, 0].corr(paired.iloc[:, 1], method=method))


def _stable_topk(frame: pd.DataFrame, topk: int) -> pd.DataFrame:
    ordered = frame.reset_index().sort_values(["score", "instrument"], ascending=[False, True], kind="stable")
    return ordered.head(min(topk, len(ordered))).set_index("instrument")


def derive_daily_signal_conversion(
    predictions: Mapping[str, pd.DataFrame],
    labels: pd.DataFrame,
    *,
    topk: int,
    minimum_cross_section: int,
    fold_assignments: Mapping[pd.Timestamp, str],
) -> pd.DataFrame:
    normalized = normalize_model_predictions(predictions, labels)
    rows: list[dict[str, object]] = []
    for model, frame in sorted(normalized.items()):
        previous_scores: pd.Series | None = None
        previous_topk: set[str] | None = None
        for date, block in frame.groupby(level="datetime", sort=True):
            normalized_date = pd.Timestamp(date).normalize()
            day = block.droplevel("datetime")[["score", "label"]].replace([np.inf, -np.inf], np.nan).dropna()
            if len(day) < minimum_cross_section:
                continue
            top = _stable_topk(day, topk)
            bottom = _stable_topk(day.assign(score=-day["score"]), topk)
            universe_mean = float(day["label"].mean())
            top_mean = float(top["label"].mean())
            bottom_mean = float(bottom["label"].mean())
            current_topk = set(top.index.astype(str))
            current_scores = day["score"]
            shared = (
                current_scores.index
                if previous_scores is None
                else current_scores.index.intersection(previous_scores.index)
            )
            rank_turnover = float("nan")
            if previous_scores is not None and len(shared) >= 2:
                before = previous_scores.loc[shared].rank(method="average", pct=True, ascending=False)
                after = current_scores.loc[shared].rank(method="average", pct=True, ascending=False)
                rank_turnover = float((before - after).abs().mean())
            overlap = float("nan")
            if previous_topk is not None:
                denominator = max(1, min(len(previous_topk), len(current_topk), topk))
                overlap = len(previous_topk & current_topk) / denominator
            top_bottom = top_mean - bottom_mean
            rows.append(
                {
                    "date": normalized_date,
                    "fold": fold_assignments.get(normalized_date),
                    "model": model,
                    "valid_count": len(day),
                    "ic": _safe_corr(day["score"], day["label"], method="pearson"),
                    "rank_ic": _safe_corr(day["score"], day["label"], method="spearman"),
                    "topk": min(topk, len(day)),
                    "topk_mean_label": top_mean,
                    "bottomk_mean_label": bottom_mean,
                    "universe_mean_label": universe_mean,
                    "topk_minus_universe": top_mean - universe_mean,
                    "topk_minus_bottomk": top_bottom,
                    "ranking_efficiency": (
                        (top_mean - universe_mean) / top_bottom
                        if np.isfinite(top_bottom) and abs(top_bottom) > 1e-15
                        else float("nan")
                    ),
                    "topk_hit_rate": float(top["label"].gt(0).mean()),
                    "topk_positive_session": float(top_mean > 0),
                    "topk_overlap_previous": overlap,
                    "rank_turnover": rank_turnover,
                    "prediction_dispersion": float(day["score"].std(ddof=1)),
                }
            )
            previous_scores = current_scores
            previous_topk = current_topk
    result = pd.DataFrame(rows)
    if result.empty or result["fold"].isna().any():
        raise ValueError("signal conversion dates are absent from certified fold assignments")
    return result.sort_values(["date", "model"], kind="stable").reset_index(drop=True)


def derive_daily_model_topk_overlap(
    predictions: Mapping[str, pd.DataFrame],
    labels: pd.DataFrame,
    *,
    topk: int,
    minimum_cross_section: int,
    fold_assignments: Mapping[pd.Timestamp, str],
) -> pd.DataFrame:
    normalized = normalize_model_predictions(predictions, labels)
    rows: list[dict[str, object]] = []
    for comparator in ("lightgbm", "ridge"):
        left = normalized["xgboost"]
        right = normalized[comparator]
        for date in left.index.get_level_values("datetime").unique().sort_values():
            xgb = left.xs(date, level="datetime")[["score", "label"]].dropna()
            other = right.xs(date, level="datetime")[["score", "label"]].dropna()
            shared = xgb.index.intersection(other.index)
            if len(shared) < minimum_cross_section:
                continue
            xgb_top = set(_stable_topk(xgb.loc[shared], topk).index.astype(str))
            other_top = set(_stable_topk(other.loc[shared], topk).index.astype(str))
            denominator = max(1, min(topk, len(xgb_top), len(other_top)))
            normalized_date = pd.Timestamp(date).normalize()
            rows.append(
                {
                    "date": normalized_date,
                    "fold": fold_assignments.get(normalized_date),
                    "pair": f"xgboost_vs_{comparator}",
                    "topk": min(topk, len(shared)),
                    "jaccard": (
                        len(xgb_top & other_top) / len(xgb_top | other_top)
                        if xgb_top | other_top
                        else float("nan")
                    ),
                    "overlap_ratio": len(xgb_top & other_top) / denominator,
                }
            )
    result = pd.DataFrame(rows)
    if result.empty or result["fold"].isna().any():
        raise ValueError("model TopK overlap dates are absent from certified fold assignments")
    return result.sort_values(["date", "pair"], kind="stable").reset_index(drop=True)


def _scope_frames(
    frame: pd.DataFrame,
    regime_labels: pd.DataFrame,
) -> list[tuple[str, str, str | None, str | None, pd.DataFrame]]:
    scopes: list[tuple[str, str, str | None, str | None, pd.DataFrame]] = [
        ("ALL_OOS", "ALL_OOS", None, None, frame)
    ]
    for fold, block in frame.groupby("fold", sort=True):
        scopes.append(("FOLD", str(fold), None, None, block))
    regimes = regime_labels.loc[
        regime_labels["status"].eq("AVAILABLE"), ["date", "dimension", "state"]
    ].copy()
    regimes["date"] = pd.to_datetime(regimes["date"]).dt.normalize()
    merged = frame.merge(regimes, on="date", how="inner", validate="many_to_many")
    for (dimension, state), block in merged.groupby(["dimension", "state"], sort=True):
        scopes.append(("REGIME", f"{dimension}:{state}", str(dimension), str(state), block))
    return scopes


def summarize_signal_conversion(
    daily: pd.DataFrame,
    regime_labels: pd.DataFrame,
) -> pd.DataFrame:
    metrics = (
        "ic",
        "rank_ic",
        "topk_mean_label",
        "bottomk_mean_label",
        "universe_mean_label",
        "topk_minus_universe",
        "topk_minus_bottomk",
        "ranking_efficiency",
        "topk_hit_rate",
        "topk_positive_session",
        "topk_overlap_previous",
        "rank_turnover",
        "prediction_dispersion",
    )
    rows: list[dict[str, object]] = []
    for scope_type, scope, dimension, state, scoped in _scope_frames(daily, regime_labels):
        for model, block in scoped.groupby("model", sort=True):
            values = {column: pd.to_numeric(block[column], errors="coerce") for column in metrics}
            rows.append(
                {
                    "scope_type": scope_type,
                    "scope": scope,
                    "dimension": dimension,
                    "state": state,
                    "model": model,
                    "sessions": int(block["date"].nunique()),
                    "valid_ic_days": int(values["ic"].notna().sum()),
                    "ic": float(values["ic"].mean()),
                    "icir": (
                        float(values["ic"].mean() / values["ic"].std(ddof=1))
                        if values["ic"].std(ddof=1) > 0
                        else float("nan")
                    ),
                    "rank_ic": float(values["rank_ic"].mean()),
                    "rank_icir": (
                        float(values["rank_ic"].mean() / values["rank_ic"].std(ddof=1))
                        if values["rank_ic"].std(ddof=1) > 0
                        else float("nan")
                    ),
                    **{column: float(values[column].mean()) for column in metrics[2:]},
                }
            )
    return (
        pd.DataFrame(rows).sort_values(["scope_type", "scope", "model"], kind="stable").reset_index(drop=True)
    )


def summarize_model_topk_overlap(
    daily: pd.DataFrame,
    regime_labels: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for scope_type, scope, dimension, state, scoped in _scope_frames(daily, regime_labels):
        for pair, block in scoped.groupby("pair", sort=True):
            rows.append(
                {
                    "scope_type": scope_type,
                    "scope": scope,
                    "dimension": dimension,
                    "state": state,
                    "pair": pair,
                    "sessions": int(block["date"].nunique()),
                    "topk": int(block["topk"].max()),
                    "jaccard_mean": float(block["jaccard"].mean()),
                    "overlap_ratio_mean": float(block["overlap_ratio"].mean()),
                }
            )
    return (
        pd.DataFrame(rows).sort_values(["scope_type", "scope", "pair"], kind="stable").reset_index(drop=True)
    )


def _one_row(frame: pd.DataFrame, **filters: object) -> pd.Series | None:
    selected = frame
    for column, value in filters.items():
        selected = selected.loc[selected[column].eq(value)]
    if len(selected) != 1:
        return None
    return selected.iloc[0]


def derive_failure_summary(
    signal: pd.DataFrame,
    model_overlap: pd.DataFrame,
    portfolio: pd.DataFrame,
    cost_sensitivity: pd.DataFrame,
    *,
    spec: FailureAttributionSpec,
) -> dict[str, object]:
    all_models = signal.loc[signal["scope_type"].eq("ALL_OOS")]
    xgb = _one_row(signal, scope_type="ALL_OOS", model="xgboost")
    failure_xgb = _one_row(signal, scope_type="FOLD", scope=spec.failure_fold, model="xgboost")
    baseline = _one_row(portfolio, scope_type="ALL_OOS", variant="baseline", model="xgboost")
    failure_portfolio = _one_row(
        portfolio,
        scope_type="FOLD",
        scope=spec.failure_fold,
        variant="baseline",
        model="xgboost",
    )
    zero_cost = _one_row(
        cost_sensitivity,
        scope_type="ALL_OOS",
        variant="baseline",
        model="xgboost",
        cost_multiplier=0.0,
    )
    base_cost = _one_row(
        cost_sensitivity,
        scope_type="ALL_OOS",
        variant="baseline",
        model="xgboost",
        cost_multiplier=1.0,
    )
    best_rank_ic = float(pd.to_numeric(all_models["rank_ic"], errors="coerce").max())
    signal_status = "PASS" if best_rank_ic >= spec.minimum_rank_ic else "WEAK"
    xgb_rank_ic = float(xgb["rank_ic"]) if xgb is not None else float("nan")
    comparator_rank_ic = float(
        pd.to_numeric(all_models.loc[all_models["model"].ne("xgboost"), "rank_ic"], errors="coerce").max()
    )
    if xgb_rank_ic >= spec.minimum_rank_ic:
        model_status = "PASS"
    elif comparator_rank_ic >= spec.minimum_rank_ic:
        model_status = "WEAK"
    else:
        model_status = "REVIEW"
    topk_excess = float(xgb["topk_minus_universe"]) if xgb is not None else float("nan")
    topk_spread = float(xgb["topk_minus_bottomk"]) if xgb is not None else float("nan")
    overlap = _one_row(
        model_overlap,
        scope_type="ALL_OOS",
        pair="xgboost_vs_lightgbm",
    )
    lgb = _one_row(signal, scope_type="ALL_OOS", model="lightgbm")
    xgb_increment = xgb_rank_ic - float(lgb["rank_ic"]) if lgb is not None else float("nan")
    topk_increment = topk_excess - float(lgb["topk_minus_universe"]) if lgb is not None else float("nan")
    near_identical = bool(
        overlap is not None
        and float(overlap["overlap_ratio_mean"]) >= spec.model_topk_near_identity
        and xgb_increment > 0
        and topk_increment <= spec.topk_increment_tolerance
    )
    ranking_status = "PASS" if topk_excess > 0 and topk_spread > 0 else "WEAK"
    if near_identical:
        ranking_status = "NO_INCREMENT"
    gross_excess = float(baseline["gross_excess_return"]) if baseline is not None else float("nan")
    net_excess = float(baseline["net_excess_return"]) if baseline is not None else float("nan")
    if np.isfinite(gross_excess) and gross_excess > 0:
        portfolio_status = "PASS" if net_excess > 0 else "COST_EXPOSED"
    elif topk_excess > 0:
        portfolio_status = "DRAG"
    else:
        portfolio_status = "REVIEW"
    zero_net = float(zero_cost["net_excess_return"]) if zero_cost is not None else float("nan")
    baseline_net = float(base_cost["net_excess_return"]) if base_cost is not None else float("nan")
    cost_drag = zero_net - baseline_net
    cost_fraction = cost_drag / gross_excess if gross_excess > 0 else float("nan")
    cost_primary = bool(
        gross_excess > 0
        and zero_net > 0
        and baseline_net <= 0
        and cost_fraction >= spec.cost_primary_gross_fraction
    )
    cost_status = (
        "PRIMARY"
        if cost_primary
        else (
            "MODERATE"
            if gross_excess > 0 and cost_fraction >= spec.cost_primary_gross_fraction
            else "NOT_PRIMARY"
        )
    )
    failure_rank_ic = float(failure_xgb["rank_ic"]) if failure_xgb is not None else float("nan")
    failure_topk = float(failure_xgb["topk_minus_universe"]) if failure_xgb is not None else float("nan")
    failure_gross = (
        float(failure_portfolio["gross_excess_return"]) if failure_portfolio is not None else float("nan")
    )
    failure_cost = float(failure_portfolio["cost_return"]) if failure_portfolio is not None else float("nan")
    if failure_rank_ic < 0 and failure_topk <= 0:
        regime_failure = "SIGNAL_DRIVEN"
    elif failure_rank_ic > 0 and failure_topk > 0 and failure_gross <= 0:
        regime_failure = "PORTFOLIO_DRIVEN"
    elif failure_gross > 0 and failure_gross - failure_cost <= 0:
        regime_failure = "COST_DRIVEN"
    else:
        regime_failure = "MIXED_OR_INCONCLUSIVE"
    full_signal_positive = bool(xgb_rank_ic > 0 and topk_excess > 0)
    if cost_primary:
        primary = "COST"
    elif full_signal_positive and regime_failure == "SIGNAL_DRIVEN" and failure_rank_ic < xgb_rank_ic:
        primary = "REGIME"
    elif portfolio_status == "DRAG":
        primary = "PORTFOLIO"
    elif ranking_status in {"WEAK", "NO_INCREMENT"} and signal_status == "PASS":
        primary = "RANKING"
    elif model_status == "WEAK":
        primary = "MODEL"
    elif signal_status == "WEAK":
        primary = "SIGNAL"
    else:
        primary = "MIXED"
    if primary not in PRIMARY_LOSS_SOURCES:
        raise RuntimeError(f"invalid primary loss source: {primary}")
    return {
        "signalLayer": {
            "status": signal_status,
            "bestModelRankIc": best_rank_ic,
            "minimumRankIc": spec.minimum_rank_ic,
        },
        "modelLayer": {
            "status": model_status,
            "xgboostRankIc": xgb_rank_ic,
            "bestComparatorRankIc": comparator_rank_ic,
        },
        "rankingLayer": {
            "status": ranking_status,
            "xgboostTopkMinusUniverse": topk_excess,
            "xgboostTopkMinusBottomk": topk_spread,
            "xgboostRankIcIncrementVsLightgbm": xgb_increment,
            "xgboostTopkIncrementVsLightgbm": topk_increment,
        },
        "portfolioLayer": {
            "status": portfolio_status,
            "grossExcessReturn": gross_excess,
            "netExcessReturn": net_excess,
        },
        "costLayer": {
            "status": cost_status,
            "zeroCostNetExcessReturn": zero_net,
            "baselineNetExcessReturn": baseline_net,
            "costDragAsGrossExcessFraction": cost_fraction,
        },
        "regimeFailure": {spec.failure_fold: regime_failure},
        "primaryAlphaLossSource": primary,
    }
