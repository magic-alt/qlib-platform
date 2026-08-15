from __future__ import annotations

import numpy as np
import pandas as pd

from tushare_qlib.lineage import sha256_json
from tushare_qlib.research.factor_clusters import (
    build_feature_clusters,
    mean_daily_rank_correlation,
)
from tushare_qlib.research.factor_taxonomy import FactorTaxonomy, FactorTaxonomyEntry


def _taxonomy() -> FactorTaxonomy:
    entries = {
        "A": FactorTaxonomyEntry("A", "Momentum", "alpha", "positive"),
        "B": FactorTaxonomyEntry("B", "Momentum", "alpha", "positive"),
        "C": FactorTaxonomyEntry("C", "Momentum", "alpha", "unknown"),
        "D": FactorTaxonomyEntry("D", "Value", "alpha", "unknown"),
        "SUPPORT": FactorTaxonomyEntry("SUPPORT", "StateSupport", "support", "unknown"),
    }
    return FactorTaxonomy("test", "pack", entries, sha256_json({}), "file")


def test_daily_rank_correlation_and_within_family_union_find():
    dates = pd.bdate_range("2025-01-02", periods=3)
    instruments = [f"S{number}" for number in range(8)]
    index = pd.MultiIndex.from_product([dates, instruments], names=["datetime", "instrument"])
    ascending = np.tile(np.arange(8, dtype=float), len(dates))
    weak = np.tile(np.array([0, 7, 1, 6, 2, 5, 3, 4], dtype=float), len(dates))
    features = pd.DataFrame(
        {
            "A": ascending,
            "B": ascending + 0.01,
            "C": weak,
            "D": ascending,
            "SUPPORT": ascending,
        },
        index=index,
    )

    correlations = mean_daily_rank_correlation(features, min_cross_section=5)
    clusters = build_feature_clusters(correlations, _taxonomy(), threshold=0.85)
    memberships = [set(item["members"]) for item in clusters["clusters"]]

    assert len(correlations) == 10
    assert {"A", "B"} in memberships
    assert not any({"A", "D"}.issubset(members) for members in memberships)
    assert not any("SUPPORT" in members for members in memberships)
    pair = correlations.set_index(["feature_a", "feature_b"])
    assert pair.loc[("A", "B"), "mean_rank_corr"] > 0.95
    assert abs(pair.loc[("A", "C"), "mean_rank_corr"]) < 0.85
