from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd
import yaml

from qlib_platform.lineage import sha256_json
from qlib_platform.data.store import sha256_file


SYNTHESIS_SCHEMA = "ashare_phase1_synthesis_v1"
RECOMMENDATIONS = (
    "PORTFOLIO_CONSTRUCTION",
    "REGIME_AWARE_RESEARCH",
    "XGBOOST_TUNING",
    "ALPHA_PACK_V2",
    "NO_GO_NEW_ALPHA",
)


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
class Phase1SynthesisSpec:
    synthesis_id: str
    recommendation_priority: tuple[str, ...]
    minimum_oriented_rank_ic: float
    minimum_positive_fold_ratio: float
    minimum_hac_t: float
    minimum_coverage: float
    minimum_unstable_feature_fraction: float
    minimum_redundant_stable_features: int
    minimum_regime_rank_ic: float
    minimum_regime_positive_fold_ratio: float
    minimum_regime_valid_folds: int
    semantic_sha256: str
    file_sha256: str

    def to_manifest(self) -> dict[str, object]:
        return {
            "schema": SYNTHESIS_SCHEMA,
            "synthesisId": self.synthesis_id,
            "recommendationPriority": list(self.recommendation_priority),
            "stableFeature": {
                "minimumOrientedRankIc": self.minimum_oriented_rank_ic,
                "minimumPositiveFoldRatio": self.minimum_positive_fold_ratio,
                "minimumHacT": self.minimum_hac_t,
                "minimumCoverage": self.minimum_coverage,
            },
            "dilution": {
                "minimumUnstableFeatureFraction": self.minimum_unstable_feature_fraction,
                "minimumRedundantStableFeatures": self.minimum_redundant_stable_features,
            },
            "repeatableRegime": {
                "minimumRankIc": self.minimum_regime_rank_ic,
                "minimumPositiveFoldRatio": self.minimum_regime_positive_fold_ratio,
                "minimumValidFolds": self.minimum_regime_valid_folds,
            },
            "semanticSha256": self.semantic_sha256,
            "fileSha256": self.file_sha256,
        }


def load_phase1_synthesis_spec(path: str | Path) -> Phase1SynthesisSpec:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Phase 1 synthesis config is missing: {source}")
    raw = _mapping(yaml.safe_load(source.read_text(encoding="utf-8")), "Phase 1 synthesis config")
    if raw.get("schema") != SYNTHESIS_SCHEMA:
        raise ValueError(f"unsupported Phase 1 synthesis schema: {raw.get('schema')}")
    synthesis_id = str(raw.get("synthesisId") or "").strip()
    if not synthesis_id:
        raise ValueError("synthesisId is required")
    priority = tuple(str(value) for value in raw.get("recommendationPriority", ()))
    if priority != RECOMMENDATIONS:
        raise ValueError(f"recommendationPriority must remain {list(RECOMMENDATIONS)}")
    stable = _mapping(raw.get("stableFeature"), "stableFeature")
    dilution = _mapping(raw.get("dilution"), "dilution")
    regime = _mapping(raw.get("repeatableRegime"), "repeatableRegime")
    minimum_oriented_rank_ic = float(str(stable.get("minimumOrientedRankIc")))
    minimum_positive_fold_ratio = _unit_interval(
        stable.get("minimumPositiveFoldRatio"), "minimumPositiveFoldRatio"
    )
    minimum_hac_t = float(str(stable.get("minimumHacT")))
    minimum_coverage = _unit_interval(stable.get("minimumCoverage"), "minimumCoverage")
    minimum_unstable_feature_fraction = _unit_interval(
        dilution.get("minimumUnstableFeatureFraction"), "minimumUnstableFeatureFraction"
    )
    minimum_redundant_stable_features = _positive_int(
        dilution.get("minimumRedundantStableFeatures"), "minimumRedundantStableFeatures"
    )
    minimum_regime_rank_ic = float(str(regime.get("minimumRankIc")))
    minimum_regime_positive_fold_ratio = _unit_interval(
        regime.get("minimumPositiveFoldRatio"), "repeatableRegime.minimumPositiveFoldRatio"
    )
    minimum_regime_valid_folds = _positive_int(
        regime.get("minimumValidFolds"), "repeatableRegime.minimumValidFolds"
    )
    if minimum_hac_t <= 0:
        raise ValueError("minimumHacT must be positive")
    semantic: dict[str, object] = {
        "schema": SYNTHESIS_SCHEMA,
        "synthesisId": synthesis_id,
        "recommendationPriority": list(priority),
        "stableFeature": {
            "minimumOrientedRankIc": minimum_oriented_rank_ic,
            "minimumPositiveFoldRatio": minimum_positive_fold_ratio,
            "minimumHacT": minimum_hac_t,
            "minimumCoverage": minimum_coverage,
        },
        "dilution": {
            "minimumUnstableFeatureFraction": minimum_unstable_feature_fraction,
            "minimumRedundantStableFeatures": minimum_redundant_stable_features,
        },
        "repeatableRegime": {
            "minimumRankIc": minimum_regime_rank_ic,
            "minimumPositiveFoldRatio": minimum_regime_positive_fold_ratio,
            "minimumValidFolds": minimum_regime_valid_folds,
        },
    }
    return Phase1SynthesisSpec(
        synthesis_id=synthesis_id,
        recommendation_priority=priority,
        minimum_oriented_rank_ic=minimum_oriented_rank_ic,
        minimum_positive_fold_ratio=minimum_positive_fold_ratio,
        minimum_hac_t=minimum_hac_t,
        minimum_coverage=minimum_coverage,
        minimum_unstable_feature_fraction=minimum_unstable_feature_fraction,
        minimum_redundant_stable_features=minimum_redundant_stable_features,
        minimum_regime_rank_ic=minimum_regime_rank_ic,
        minimum_regime_positive_fold_ratio=minimum_regime_positive_fold_ratio,
        minimum_regime_valid_folds=minimum_regime_valid_folds,
        semantic_sha256=sha256_json(semantic),
        file_sha256=sha256_file(source),
    )


def derive_feature_evidence(
    feature_summary: pd.DataFrame,
    clusters: Mapping[str, Any],
    *,
    spec: Phase1SynthesisSpec,
) -> dict[str, object]:
    required = {
        "feature",
        "role",
        "orientation_available",
        "oriented_rank_ic_mean",
        "positive_oriented_rank_ic_fold_ratio",
        "rank_ic_hac_t",
        "coverage_median",
    }
    missing = required - set(feature_summary)
    if missing:
        raise ValueError(f"feature summary is missing columns: {sorted(missing)}")
    eligible = feature_summary.loc[
        feature_summary["role"].eq("alpha") & feature_summary["orientation_available"].eq(True)
    ].copy()
    stable_mask = (
        pd.to_numeric(eligible["oriented_rank_ic_mean"], errors="coerce").ge(spec.minimum_oriented_rank_ic)
        & pd.to_numeric(eligible["positive_oriented_rank_ic_fold_ratio"], errors="coerce").ge(
            spec.minimum_positive_fold_ratio
        )
        & pd.to_numeric(eligible["rank_ic_hac_t"], errors="coerce").abs().ge(spec.minimum_hac_t)
        & pd.to_numeric(eligible["coverage_median"], errors="coerce").ge(spec.minimum_coverage)
    )
    stable_features = sorted(eligible.loc[stable_mask, "feature"].astype(str))
    unstable = eligible.loc[
        pd.to_numeric(eligible["oriented_rank_ic_mean"], errors="coerce").le(0)
        | pd.to_numeric(eligible["positive_oriented_rank_ic_fold_ratio"], errors="coerce").lt(0.5)
    ]
    unstable_fraction = len(unstable) / len(eligible) if len(eligible) else float("nan")
    raw_clusters = clusters.get("clusters")
    if not isinstance(raw_clusters, list):
        raise ValueError("feature cluster artifact contains no clusters")
    redundant_stable: set[str] = set()
    for raw in raw_clusters:
        cluster = _mapping(raw, "feature cluster")
        members = {str(value) for value in cluster.get("members", ())}
        if len(members) > 1:
            redundant_stable.update(members.intersection(stable_features))
    dilution = bool(
        len(redundant_stable) >= spec.minimum_redundant_stable_features
        or np.isfinite(unstable_fraction)
        and unstable_fraction >= spec.minimum_unstable_feature_fraction
    )
    return {
        "eligibleAlphaFeatures": len(eligible),
        "stableFeatureCount": len(stable_features),
        "stableFeatures": stable_features,
        "unstableFeatureCount": len(unstable),
        "unstableFeatureFraction": unstable_fraction,
        "redundantStableFeatureCount": len(redundant_stable),
        "redundantStableFeatures": sorted(redundant_stable),
        "redundancyOrUnstableFeatureDilution": dilution,
    }


def derive_regime_evidence(
    model_regime: pd.DataFrame,
    *,
    spec: Phase1SynthesisSpec,
) -> dict[str, object]:
    required = {
        "model",
        "dimension",
        "state",
        "sample_status",
        "rank_ic_mean",
        "positive_rank_ic_fold_ratio",
        "valid_folds",
    }
    missing = required - set(model_regime)
    if missing:
        raise ValueError(f"model regime diagnostics are missing columns: {sorted(missing)}")
    xgb = model_regime.loc[
        model_regime["model"].eq("xgboost") & model_regime["sample_status"].eq("SUFFICIENT")
    ].copy()
    repeatable = xgb.loc[
        pd.to_numeric(xgb["rank_ic_mean"], errors="coerce").ge(spec.minimum_regime_rank_ic)
        & pd.to_numeric(xgb["positive_rank_ic_fold_ratio"], errors="coerce").ge(
            spec.minimum_regime_positive_fold_ratio
        )
        & pd.to_numeric(xgb["valid_folds"], errors="coerce").ge(spec.minimum_regime_valid_folds)
    ]
    rows = [
        {
            "dimension": str(row.dimension),
            "state": str(row.state),
            "rankIcMean": float(row.rank_ic_mean),
            "positiveFoldRatio": float(row.positive_rank_ic_fold_ratio),
            "validFolds": int(row.valid_folds),
        }
        for row in repeatable.sort_values(["dimension", "state"], kind="stable").itertuples(index=False)
    ]
    return {
        "sufficientXgbRegimeStates": len(xgb),
        "repeatableConditionalStateCount": len(rows),
        "repeatableConditionalStates": rows,
    }


def _candidate(
    recommendation: str,
    rule_id: str,
    eligible: bool,
    *,
    supporting: list[str],
    counter: list[str],
    gaps: list[str],
    rejection: list[str],
) -> dict[str, object]:
    return {
        "recommendation": recommendation,
        "eligible": eligible,
        "ruleId": rule_id,
        "supportingEvidence": supporting,
        "counterEvidence": counter,
        "gaps": gaps,
        "rejectionReasons": rejection,
    }


def derive_phase1_recommendation(
    *,
    failure_summary: Mapping[str, Any],
    explanation_summary: Mapping[str, Any],
    feature_evidence: Mapping[str, Any],
    regime_evidence: Mapping[str, Any],
    spec: Phase1SynthesisSpec,
) -> dict[str, object]:
    loss_source = str(failure_summary.get("primaryAlphaLossSource") or "")
    if loss_source not in {"SIGNAL", "MODEL", "RANKING", "PORTFOLIO", "COST", "REGIME", "MIXED"}:
        raise ValueError(f"invalid primary Alpha loss source: {loss_source}")
    signal = _mapping(failure_summary.get("signalLayer"), "signalLayer")
    ranking = _mapping(failure_summary.get("rankingLayer"), "rankingLayer")
    portfolio = _mapping(failure_summary.get("portfolioLayer"), "portfolioLayer")
    cost = _mapping(failure_summary.get("costLayer"), "costLayer")
    signal_available = signal.get("status") == "PASS"
    implementation_drag = bool(
        (loss_source == "COST" and cost.get("status") in {"PRIMARY", "MODERATE"})
        or (loss_source == "PORTFOLIO" and portfolio.get("status") in {"DRAG", "COST_EXPOSED"})
        or (loss_source == "RANKING" and ranking.get("status") in {"WEAK", "NO_INCREMENT"})
    )
    portfolio_eligible = (
        loss_source in {"COST", "PORTFOLIO", "RANKING"} and signal_available and implementation_drag
    )
    repeatable_regime = int(regime_evidence.get("repeatableConditionalStateCount") or 0) > 0
    regime_eligible = loss_source == "REGIME" and repeatable_regime
    bounded = str(explanation_summary.get("boundedSensitivity") or "")
    stable_structure = explanation_summary.get("stableSignalStructure") is True
    tuning_eligible = loss_source in {"MODEL", "MIXED"} and stable_structure and bounded == "RECOVERABLE"
    stable_features = int(feature_evidence.get("stableFeatureCount") or 0)
    dilution = feature_evidence.get("redundancyOrUnstableFeatureDilution") is True
    alpha_pack_eligible = stable_features > 0 and dilution
    candidates = [
        _candidate(
            "PORTFOLIO_CONSTRUCTION",
            "R1_IMPLEMENTATION_DRAG",
            portfolio_eligible,
            supporting=(
                [f"PRIMARY_ALPHA_LOSS_SOURCE={loss_source}", f"signalLayer={signal.get('status')}"]
                if portfolio_eligible
                else []
            ),
            counter=[] if signal_available else ["signal layer is not PASS"],
            gaps=[],
            rejection=[] if portfolio_eligible else ["quantified implementation drag gate is not met"],
        ),
        _candidate(
            "REGIME_AWARE_RESEARCH",
            "R2_REPEATABLE_CAUSAL_REGIME",
            regime_eligible,
            supporting=(
                [
                    "PRIMARY_ALPHA_LOSS_SOURCE=REGIME",
                    f"repeatableConditionalStateCount={regime_evidence.get('repeatableConditionalStateCount')}",
                ]
                if regime_eligible
                else []
            ),
            counter=[],
            gaps=[],
            rejection=[] if regime_eligible else ["repeatable causal regime gate is not met"],
        ),
        _candidate(
            "XGBOOST_TUNING",
            "R3_RECOVERABLE_MODEL",
            tuning_eligible,
            supporting=(
                [f"PRIMARY_ALPHA_LOSS_SOURCE={loss_source}", "stableSignalStructure=true"]
                if tuning_eligible
                else []
            ),
            counter=[] if stable_structure else ["stable XGBoost-specific structure is absent"],
            gaps=[] if bounded == "RECOVERABLE" else ["BOUNDED_SENSITIVITY_NOT_RECOVERABLE"],
            rejection=[] if tuning_eligible else ["recoverable bounded-sensitivity gate is not met"],
        ),
        _candidate(
            "ALPHA_PACK_V2",
            "R4_STABLE_SIGNAL_DILUTION",
            alpha_pack_eligible,
            supporting=(
                [f"stableFeatureCount={stable_features}", "redundancyOrUnstableFeatureDilution=true"]
                if alpha_pack_eligible
                else []
            ),
            counter=[] if stable_features > 0 else ["no stable feature meets the predeclared threshold"],
            gaps=[],
            rejection=[] if alpha_pack_eligible else ["stable-signal dilution gate is not met"],
        ),
    ]
    eligible_before_fallback = any(bool(item["eligible"]) for item in candidates)
    candidates.append(
        _candidate(
            "NO_GO_NEW_ALPHA",
            "R5_FALLBACK_WEAK_OR_UNCERTAIN",
            not eligible_before_fallback,
            supporting=(
                [f"PRIMARY_ALPHA_LOSS_SOURCE={loss_source}", "no prior action clears its evidence gate"]
                if not eligible_before_fallback
                else []
            ),
            counter=[],
            gaps=[],
            rejection=[]
            if not eligible_before_fallback
            else ["a higher-priority action clears its evidence gate"],
        )
    )
    by_name = {str(item["recommendation"]): item for item in candidates}
    primary = next(
        recommendation
        for recommendation in spec.recommendation_priority
        if bool(by_name[recommendation]["eligible"])
    )
    selected = by_name[primary]
    return {
        "primaryRecommendation": primary,
        "recommendationRuleId": selected["ruleId"],
        "candidateAssessment": candidates,
        "decisionEvidence": {
            "supportingEvidence": selected["supportingEvidence"],
            "counterEvidence": selected["counterEvidence"],
            "knownGaps": selected["gaps"],
            "rejectedHypotheses": [
                key
                for key, value in _mapping(explanation_summary.get("hypotheses"), "hypotheses").items()
                if _mapping(value, f"hypothesis {key}").get("status") == "REJECTED"
            ],
        },
        "evidence": {
            "primaryAlphaLossSource": loss_source,
            "feature": dict(feature_evidence),
            "regime": dict(regime_evidence),
            "modelExplanation": dict(explanation_summary),
        },
    }
