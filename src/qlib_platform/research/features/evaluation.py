from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from qlib_platform.data.store import sha256_file
from qlib_platform.lineage import git_revision, sha256_json
from qlib_platform.research.features.clusters import mean_daily_rank_correlation
from qlib_platform.research.features.registry import FactorRegistry


@dataclass(frozen=True)
class FactorEvaluationPolicy:
    min_cross_section: int = 30
    min_coverage: float = 0.80
    min_oriented_ic: float = 0.005
    min_oriented_rank_ic: float = 0.010
    min_oriented_rank_icir: float = 0.15
    max_rank_turnover: float = 0.35
    max_pairwise_rank_corr: float = 0.85
    min_incremental_rank_ic: float = 0.0
    require_incremental: bool = False

    def __post_init__(self) -> None:
        if self.min_cross_section < 2:
            raise ValueError("min_cross_section must be at least 2")
        if not 0 <= self.min_coverage <= 1:
            raise ValueError("min_coverage must be in [0, 1]")
        if not 0 <= self.max_rank_turnover <= 1:
            raise ValueError("max_rank_turnover must be in [0, 1]")
        if not 0 < self.max_pairwise_rank_corr <= 1:
            raise ValueError("max_pairwise_rank_corr must be in (0, 1]")


@dataclass(frozen=True)
class FactorEvaluationResult:
    daily: pd.DataFrame
    summary: pd.DataFrame
    decay: pd.DataFrame
    correlations: pd.DataFrame
    clusters: dict[str, object]
    neutralized_features: pd.DataFrame
    exposure_columns: tuple[str, ...]


def _require_panel(frame: pd.DataFrame, name: str) -> None:
    if not isinstance(frame.index, pd.MultiIndex) or frame.index.names != ["datetime", "instrument"]:
        raise ValueError(f"{name} requires a datetime/instrument MultiIndex")
    if frame.index.has_duplicates:
        raise ValueError(f"{name} contains duplicate datetime/instrument keys")


def _label_series(labels: pd.Series | pd.DataFrame) -> pd.Series:
    if isinstance(labels, pd.DataFrame):
        if "label" in labels:
            series = labels["label"]
        elif labels.shape[1] == 1:
            series = labels.iloc[:, 0]
        else:
            raise ValueError("labels must contain exactly one label column")
    else:
        series = labels
    frame = series.rename("label").to_frame()
    _require_panel(frame, "labels")
    return pd.to_numeric(series, errors="coerce").rename("label").sort_index()


def _safe_corr(left: pd.Series, right: pd.Series, method: str) -> float:
    pair = pd.concat([left, right], axis=1).dropna()
    if len(pair) < 2 or pair.iloc[:, 0].nunique() < 2 or pair.iloc[:, 1].nunique() < 2:
        return float("nan")
    return float(pair.iloc[:, 0].corr(pair.iloc[:, 1], method=method))


def neutralize_cross_section(
    features: pd.DataFrame,
    exposures: pd.DataFrame | None,
    *,
    min_cross_section: int,
) -> pd.DataFrame:
    """Cross-sectionally residualize factors on predeclared exposures plus an intercept."""

    _require_panel(features, "features")
    numeric = features.apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan)
    if exposures is None:
        return numeric
    _require_panel(exposures, "exposures")
    aligned_exposures = (
        exposures.reindex(numeric.index)
        .apply(pd.to_numeric, errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
    )
    result = pd.DataFrame(index=numeric.index, columns=numeric.columns, dtype=float)
    for date, block in numeric.groupby(level="datetime", sort=True):
        day_x = aligned_exposures.xs(date, level="datetime").reindex(block.droplevel("datetime").index)
        instruments = block.droplevel("datetime").index
        exposure_values = day_x.to_numpy(dtype=float)
        for factor in numeric.columns:
            y = block[factor].to_numpy(dtype=float)
            valid = np.isfinite(y) & np.isfinite(exposure_values).all(axis=1)
            residual = np.full(len(y), np.nan, dtype=float)
            if int(valid.sum()) >= max(min_cross_section, exposure_values.shape[1] + 2):
                design = np.column_stack([np.ones(int(valid.sum()), dtype=float), exposure_values[valid]])
                beta, *_ = np.linalg.lstsq(design, y[valid], rcond=None)
                residual[valid] = y[valid] - design @ beta
            keys = pd.MultiIndex.from_arrays(
                [[pd.Timestamp(date)] * len(instruments), instruments],
                names=["datetime", "instrument"],
            )
            result.loc[keys, factor] = residual
    return result


def _daily_metrics(
    features: pd.DataFrame,
    label: pd.Series,
    registry: FactorRegistry,
    *,
    min_cross_section: int,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for date, block in features.groupby(level="datetime", sort=True):
        day = block.droplevel("datetime")
        day_label = label.xs(date, level="datetime").reindex(day.index)
        universe = len(day)
        for name in registry.names:
            factor = pd.to_numeric(day[name], errors="coerce")
            pair = pd.concat([factor.rename("factor"), day_label.rename("label")], axis=1).dropna()
            valid = len(pair)
            eligible = valid >= min_cross_section
            definition = registry.get(name)
            rows.append(
                {
                    "date": pd.Timestamp(date).normalize(),
                    "factor": name,
                    "family": definition.family,
                    "role": definition.role,
                    "direction": definition.direction,
                    "universe_count": universe,
                    "valid_count": valid,
                    "coverage": valid / universe if universe else float("nan"),
                    "ic": _safe_corr(pair["factor"], pair["label"], "pearson") if eligible else float("nan"),
                    "rank_ic": _safe_corr(pair["factor"], pair["label"], "spearman") if eligible else float("nan"),
                }
            )
    return pd.DataFrame(rows).sort_values(["date", "factor"], kind="stable").reset_index(drop=True)


def _ratio(values: pd.Series) -> float:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if len(clean) < 2:
        return float("nan")
    std = float(clean.std(ddof=1))
    return float(clean.mean() / std) if np.isfinite(std) and std > 0 else float("nan")


def _rank_turnover(features: pd.DataFrame) -> dict[str, float]:
    ranks = features.groupby(level="datetime", sort=True).rank(pct=True, method="average")
    dates = pd.DatetimeIndex(ranks.index.get_level_values("datetime").unique()).sort_values()
    values: dict[str, list[float]] = {str(column): [] for column in ranks.columns}
    for previous, current in zip(dates[:-1], dates[1:]):
        left = ranks.xs(previous, level="datetime")
        right = ranks.xs(current, level="datetime")
        common = left.index.intersection(right.index)
        if len(common) < 2:
            continue
        difference = (right.loc[common] - left.loc[common]).abs()
        for column in ranks.columns:
            value = float(difference[column].mean())
            if np.isfinite(value):
                values[str(column)].append(value)
    return {name: (float(np.mean(items)) if items else float("nan")) for name, items in values.items()}


def _incremental_rank_ic(
    features: pd.DataFrame,
    label: pd.Series,
    baseline: pd.Series | pd.DataFrame | None,
    registry: FactorRegistry,
    *,
    min_cross_section: int,
) -> dict[str, float]:
    if baseline is None:
        return {name: float("nan") for name in registry.names}
    if isinstance(baseline, pd.DataFrame):
        if "score" in baseline:
            baseline_score = baseline["score"]
        elif baseline.shape[1] == 1:
            baseline_score = baseline.iloc[:, 0]
        else:
            raise ValueError("baseline must contain exactly one score column")
    else:
        baseline_score = baseline
    baseline_score = pd.to_numeric(baseline_score, errors="coerce").reindex(features.index)
    deltas: dict[str, list[float]] = {name: [] for name in registry.names}
    for date, block in features.groupby(level="datetime", sort=True):
        factor_block = block.droplevel("datetime")
        day_label = label.xs(date, level="datetime").reindex(factor_block.index)
        day_base = baseline_score.xs(date, level="datetime").reindex(factor_block.index)
        for name in registry.names:
            direction = registry.get(name).direction
            joined = pd.concat(
                [
                    day_base.rename("base"),
                    factor_block[name].rename("factor"),
                    day_label.rename("label"),
                ],
                axis=1,
            ).dropna()
            if len(joined) < min_cross_section:
                continue
            if min(joined["base"].nunique(), joined["factor"].nunique(), joined["label"].nunique()) < 2:
                continue
            base_rank = joined["base"].rank(pct=True)
            factor_rank = joined["factor"].rank(pct=True) * direction
            combined = (base_rank + factor_rank) / 2.0
            base_ic = float(base_rank.corr(joined["label"], method="spearman"))
            combined_ic = float(combined.corr(joined["label"], method="spearman"))
            if np.isfinite(base_ic) and np.isfinite(combined_ic):
                deltas[name].append(combined_ic - base_ic)
    return {name: (float(np.mean(items)) if items else float("nan")) for name, items in deltas.items()}


def _decay_table(
    features: pd.DataFrame,
    registry: FactorRegistry,
    labels_by_horizon: Mapping[int, pd.Series | pd.DataFrame],
    *,
    min_cross_section: int,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for horizon, raw_labels in sorted(labels_by_horizon.items()):
        if int(horizon) < 1:
            raise ValueError("decay horizons must be positive")
        labels = _label_series(raw_labels).reindex(features.index)
        daily = _daily_metrics(features, labels, registry, min_cross_section=min_cross_section)
        for factor, block in daily.groupby("factor", sort=True):
            definition = registry.get(str(factor))
            rows.append(
                {
                    "factor": factor,
                    "horizon": int(horizon),
                    "ic_mean": float(block["ic"].mean()),
                    "rank_ic_mean": float(block["rank_ic"].mean()),
                    "oriented_rank_ic_mean": float(block["rank_ic"].mean()) * definition.direction,
                    "valid_days": int(block["rank_ic"].notna().sum()),
                }
            )
    return pd.DataFrame(rows).sort_values(["factor", "horizon"], kind="stable").reset_index(drop=True)


def _clusters(correlations: pd.DataFrame, registry: FactorRegistry, threshold: float) -> dict[str, object]:
    parent = {name: name for name in registry.names}

    def find(name: str) -> str:
        root = parent[name]
        if root != name:
            parent[name] = find(root)
        return parent[name]

    def union(left: str, right: str) -> None:
        a, b = find(left), find(right)
        if a == b:
            return
        first, second = sorted((a, b))
        parent[second] = first

    edges: list[dict[str, object]] = []
    for row in correlations.itertuples(index=False):
        value = float(row.mean_rank_corr)
        if np.isfinite(value) and abs(value) >= threshold:
            left, right = str(row.feature_a), str(row.feature_b)
            union(left, right)
            edges.append({"factorA": left, "factorB": right, "meanRankCorrelation": value})
    groups: dict[str, list[str]] = {}
    for name in registry.names:
        groups.setdefault(find(name), []).append(name)
    clusters = [
        {"clusterId": f"factor_cluster_{index:04d}", "members": sorted(members)}
        for index, members in enumerate(
            sorted((sorted(values) for values in groups.values()), key=lambda item: item),
            start=1,
        )
    ]
    return {
        "schemaVersion": "factor_clusters_v2",
        "method": "absolute_mean_daily_rank_correlation_union_find",
        "threshold": threshold,
        "clusters": clusters,
        "edges": sorted(edges, key=lambda item: (str(item["factorA"]), str(item["factorB"]))),
    }


def _screen(
    summary: pd.DataFrame,
    correlations: pd.DataFrame,
    registry: FactorRegistry,
    policy: FactorEvaluationPolicy,
) -> pd.DataFrame:
    correlation_lookup: dict[tuple[str, str], float] = {}
    for row in correlations.itertuples(index=False):
        correlation_lookup[tuple(sorted((str(row.feature_a), str(row.feature_b))))] = float(
            row.mean_rank_corr
        )

    ranked = summary.sort_values(
        ["oriented_rank_icir", "oriented_rank_ic_mean", "factor"],
        ascending=[False, False, True],
        na_position="last",
        kind="stable",
    ).copy()
    admitted: list[str] = []
    decisions: dict[str, tuple[str, list[str], float]] = {}
    for row in ranked.itertuples(index=False):
        name = str(row.factor)
        reasons: list[str] = []
        if registry.get(name).role != "alpha":
            reasons.append("role_not_alpha")
        if not np.isfinite(row.coverage_mean) or row.coverage_mean < policy.min_coverage:
            reasons.append("coverage_below_minimum")
        if not np.isfinite(row.oriented_ic_mean) or row.oriented_ic_mean < policy.min_oriented_ic:
            reasons.append("oriented_ic_below_minimum")
        if (
            not np.isfinite(row.oriented_rank_ic_mean)
            or row.oriented_rank_ic_mean < policy.min_oriented_rank_ic
        ):
            reasons.append("oriented_rank_ic_below_minimum")
        if not np.isfinite(row.oriented_rank_icir) or row.oriented_rank_icir < policy.min_oriented_rank_icir:
            reasons.append("oriented_rank_icir_below_minimum")
        if not np.isfinite(row.rank_turnover) or row.rank_turnover > policy.max_rank_turnover:
            reasons.append("rank_turnover_above_maximum")
        if policy.require_incremental and (
            not np.isfinite(row.incremental_rank_ic)
            or row.incremental_rank_ic < policy.min_incremental_rank_ic
        ):
            reasons.append("incremental_rank_ic_below_minimum")

        max_corr = 0.0
        for selected in admitted:
            value = correlation_lookup.get(tuple(sorted((name, selected))), float("nan"))
            if np.isfinite(value):
                max_corr = max(max_corr, abs(value))
        if max_corr >= policy.max_pairwise_rank_corr:
            reasons.append("correlated_with_admitted_factor")

        decision = "ADMIT" if not reasons else "REJECT"
        if decision == "ADMIT":
            admitted.append(name)
        decisions[name] = (decision, reasons, max_corr)

    result = summary.copy()
    result["decision"] = result["factor"].map(lambda name: decisions[str(name)][0])
    result["decision_reasons"] = result["factor"].map(lambda name: ",".join(decisions[str(name)][1]))
    result["max_abs_corr_to_admitted"] = result["factor"].map(lambda name: decisions[str(name)][2])
    return result.sort_values("factor", kind="stable").reset_index(drop=True)


def evaluate_factors(
    features: pd.DataFrame,
    labels: pd.Series | pd.DataFrame,
    registry: FactorRegistry,
    *,
    policy: FactorEvaluationPolicy | None = None,
    exposures: pd.DataFrame | None = None,
    baseline: pd.Series | pd.DataFrame | None = None,
    decay_labels: Mapping[int, pd.Series | pd.DataFrame] | None = None,
) -> FactorEvaluationResult:
    policy = policy or FactorEvaluationPolicy()
    _require_panel(features, "features")
    registry.validate_columns(features.columns)
    selected = features.loc[:, list(registry.names)].sort_index()
    label = _label_series(labels)
    missing_labels = selected.index.difference(label.index)
    if len(missing_labels):
        raise ValueError(f"labels are missing {len(missing_labels)} feature rows")
    label = label.reindex(selected.index)
    neutralized = neutralize_cross_section(
        selected,
        exposures,
        min_cross_section=policy.min_cross_section,
    )
    daily = _daily_metrics(
        neutralized,
        label,
        registry,
        min_cross_section=policy.min_cross_section,
    )
    turnover = _rank_turnover(neutralized)
    incremental = _incremental_rank_ic(
        neutralized,
        label,
        baseline,
        registry,
        min_cross_section=policy.min_cross_section,
    )
    rows: list[dict[str, object]] = []
    for factor, block in daily.groupby("factor", sort=True):
        definition = registry.get(str(factor))
        ic_mean = float(block["ic"].mean())
        rank_ic_mean = float(block["rank_ic"].mean())
        rows.append(
            {
                "factor": factor,
                "family": definition.family,
                "role": definition.role,
                "direction": definition.direction,
                "sessions": int(block["date"].nunique()),
                "valid_ic_days": int(block["ic"].notna().sum()),
                "valid_rank_ic_days": int(block["rank_ic"].notna().sum()),
                "coverage_mean": float(block["coverage"].mean()),
                "coverage_median": float(block["coverage"].median()),
                "ic_mean": ic_mean,
                "rank_ic_mean": rank_ic_mean,
                "icir": _ratio(block["ic"]),
                "rank_icir": _ratio(block["rank_ic"]),
                "oriented_ic_mean": ic_mean * definition.direction,
                "oriented_rank_ic_mean": rank_ic_mean * definition.direction,
                "oriented_rank_icir": _ratio(block["rank_ic"] * definition.direction),
                "positive_oriented_rank_ic_ratio": float(
                    (block["rank_ic"] * definition.direction).dropna().gt(0).mean()
                ),
                "rank_turnover": turnover[str(factor)],
                "incremental_rank_ic": incremental[str(factor)],
            }
        )
    summary = pd.DataFrame(rows)

    correlations = mean_daily_rank_correlation(
        neutralized,
        min_cross_section=policy.min_cross_section,
    )
    clusters = _clusters(correlations, registry, policy.max_pairwise_rank_corr)
    summary = _screen(summary, correlations, registry, policy)
    all_decay = {1: label}
    if decay_labels:
        all_decay.update({int(key): value for key, value in decay_labels.items()})
    decay = _decay_table(
        neutralized,
        registry,
        all_decay,
        min_cross_section=policy.min_cross_section,
    )
    return FactorEvaluationResult(
        daily=daily,
        summary=summary,
        decay=decay,
        correlations=correlations,
        clusters=clusters,
        neutralized_features=neutralized,
        exposure_columns=tuple(str(column) for column in exposures.columns) if exposures is not None else (),
    )


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(handle)
    temp = Path(temporary)
    try:
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def write_factor_evaluation(
    output_root: str | Path,
    result: FactorEvaluationResult,
    *,
    registry: FactorRegistry,
    policy: FactorEvaluationPolicy,
    dataset_version_id: str,
    feature_snapshot_id: str,
    label_spec_id: str,
    baseline_prediction_id: str | None = None,
    final_holdout_accessed: bool = False,
    repository_root: str | Path | None = None,
) -> Path:
    """Write immutable screening evidence without creating a governed candidate."""

    required = {
        "dataset_version_id": dataset_version_id,
        "feature_snapshot_id": feature_snapshot_id,
        "label_spec_id": label_spec_id,
    }
    missing = sorted(key for key, value in required.items() if not value.strip())
    if missing:
        raise ValueError(f"factor evaluation identity is incomplete: {missing}")
    if final_holdout_accessed:
        raise ValueError("factor screening cannot consume the final holdout")

    revision = git_revision(Path(repository_root or Path(__file__).resolve().parents[4]))
    commit = str(revision.get("commit") or "").strip()
    if not commit:
        raise ValueError("Git revision is required for factor evaluation identity")
    identity = {
        "schemaVersion": "factor_evaluation_v1",
        "datasetVersionId": dataset_version_id,
        "featureSnapshotId": feature_snapshot_id,
        "labelSpecId": label_spec_id,
        "baselinePredictionId": baseline_prediction_id,
        "neutralizationExposureColumns": list(result.exposure_columns),
        "factorRegistryId": registry.registry_id,
        "factorRegistrySha256": registry.semantic_sha256,
        "policy": asdict(policy),
        "codeCommit": commit,
        "codeDirty": revision.get("dirty"),
        "finalHoldoutAccessed": False,
        "formalCandidateCreated": False,
        "publishingAuthorized": False,
    }
    evaluation_id = "factor_eval_" + sha256_json(identity)[:24]
    root = Path(output_root) / evaluation_id
    manifest_path = root / "factor_evaluation_manifest.json"
    if root.exists():
        if not manifest_path.is_file():
            raise ValueError(f"incomplete immutable factor evaluation exists: {root}")
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if {key: existing.get(key) for key in identity} != identity:
            raise ValueError(f"factor evaluation identity mismatch: {root}")
        artifacts = existing.get("artifacts")
        if not isinstance(artifacts, Mapping):
            raise ValueError("factor evaluation manifest has no artifact checksums")
        for name, expected in artifacts.items():
            path = root / str(name)
            if not path.is_file() or sha256_file(path) != expected:
                raise ValueError(f"factor evaluation checksum mismatch: {name}")
        return manifest_path

    parent = Path(output_root)
    parent.mkdir(parents=True, exist_ok=True)
    building = Path(tempfile.mkdtemp(prefix=".factor-eval-building-", dir=parent))
    try:
        files = {
            "factor_daily.parquet": result.daily,
            "factor_summary.parquet": result.summary,
            "factor_decay.parquet": result.decay,
            "factor_correlations.parquet": result.correlations,
            "factor_neutralized.parquet": result.neutralized_features.reset_index(),
        }
        for name, frame in files.items():
            frame.to_parquet(building / name, index=False)
        _atomic_json(building / "factor_clusters.json", result.clusters)
        checksums = {name: sha256_file(building / name) for name in [*files, "factor_clusters.json"]}
        manifest = {
            **identity,
            "evaluationId": evaluation_id,
            "artifacts": checksums,
            "decisionSemantics": (
                "ADMIT/REJECT is validation-screening evidence only. It does not create a "
                "formal research candidate, authorize model selection under Phase 3-D, "
                "open the final holdout, or publish a portfolio."
            ),
        }
        _atomic_json(building / manifest_path.name, manifest)
        os.replace(building, root)
        return root / manifest_path.name
    finally:
        if building.exists():
            shutil.rmtree(building, ignore_errors=True)
