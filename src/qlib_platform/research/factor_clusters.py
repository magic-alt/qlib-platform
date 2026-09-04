from __future__ import annotations

from collections import defaultdict

import numpy as np
import pandas as pd

from .factor_taxonomy import FactorTaxonomy


def mean_daily_rank_correlation(
    features: pd.DataFrame,
    *,
    min_cross_section: int,
) -> pd.DataFrame:
    """Mean daily pairwise Spearman correlation over the rolling OOS panel."""

    names = [str(value) for value in features.columns]
    size = len(names)
    totals = np.zeros((size, size), dtype=float)
    valid_days = np.zeros((size, size), dtype=np.int64)
    numeric = features.apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan)
    for _, block in numeric.groupby(level="datetime", sort=True):
        matrix = block.corr(method="spearman", min_periods=min_cross_section).to_numpy(dtype=float)
        valid = np.isfinite(matrix)
        totals[valid] += matrix[valid]
        valid_days[valid] += 1

    rows: list[dict[str, object]] = []
    for left in range(size):
        for right in range(left + 1, size):
            count = int(valid_days[left, right])
            value = float(totals[left, right] / count) if count else float("nan")
            rows.append(
                {
                    "feature_a": names[left],
                    "feature_b": names[right],
                    "mean_rank_corr": value,
                    "abs_mean_rank_corr": abs(value) if np.isfinite(value) else float("nan"),
                    "valid_day_count": count,
                }
            )
    return pd.DataFrame(rows).sort_values(["feature_a", "feature_b"], kind="stable").reset_index(drop=True)


class _UnionFind:
    def __init__(self, values: list[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        first, second = sorted((left_root, right_root))
        self.parent[second] = first


def build_feature_clusters(
    correlations: pd.DataFrame,
    taxonomy: FactorTaxonomy,
    *,
    threshold: float,
) -> dict[str, object]:
    eligible = sorted(name for name, entry in taxonomy.entries.items() if entry.ranking_eligible)
    union = _UnionFind(eligible)
    edges: list[dict[str, object]] = []
    for row in correlations.itertuples(index=False):
        left = str(row.feature_a)
        right = str(row.feature_b)
        value = float(row.mean_rank_corr)
        if left not in union.parent or right not in union.parent or not np.isfinite(value):
            continue
        left_entry = taxonomy.entry(left)
        right_entry = taxonomy.entry(right)
        if left_entry.family != right_entry.family or abs(value) < threshold:
            continue
        union.union(left, right)
        edges.append({"featureA": left, "featureB": right, "meanRankCorrelation": value})

    groups: dict[str, list[str]] = defaultdict(list)
    for feature in eligible:
        groups[union.find(feature)].append(feature)
    ordered = sorted(
        (sorted(members) for members in groups.values()),
        key=lambda item: (taxonomy.entry(item[0]).family, item),
    )
    clusters = [
        {
            "clusterId": f"cluster_{number:04d}",
            "family": taxonomy.entry(members[0]).family,
            "members": members,
        }
        for number, members in enumerate(ordered, start=1)
    ]
    excluded = {
        role: sorted(name for name, entry in taxonomy.entries.items() if entry.role == role)
        for role in ("exposure", "support")
    }
    return {
        "schemaVersion": "factor_clusters_v1",
        "method": "within_family_absolute_mean_daily_rank_correlation_union_find",
        "threshold": threshold,
        "rankingRole": "alpha",
        "clusters": clusters,
        "edges": sorted(edges, key=lambda item: (str(item["featureA"]), str(item["featureB"]))),
        "excludedFromAlphaClustering": excluded,
    }
