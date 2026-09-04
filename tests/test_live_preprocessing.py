from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from qlib_platform.live_inference import prepare_live_features
from qlib_platform.model_bundle import LoadedModelBundle


def test_live_preprocessing_reuses_bundle_state_and_pit_filter(tmp_path: Path):
    columns = ["PAUSED", "LISTED_DAYS", "CIRC_MV", "MONEY20", "IS_ST", "F0"]
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "canonical_config.yaml").write_text(
        yaml.safe_dump(
            {
                "dataset": {
                    "secondary_filters": {
                        "min_listed_days": 120,
                        "min_circ_mv_yuan": 100,
                        "min_money_20d_yuan": 10,
                        "exclude_st": True,
                        "allow_unknown_st": False,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    bundle = LoadedModelBundle(
        root=root,
        manifest={},
        feature_columns=columns,
        mean=np.zeros(len(columns)),
        scale=np.ones(len(columns)),
        model=None,
    )
    index = pd.MultiIndex.from_tuples(
        [
            (pd.Timestamp("2026-08-10"), "SH600000"),
            (pd.Timestamp("2026-08-10"), "SZ000001"),
        ],
        names=["datetime", "instrument"],
    )
    raw = pd.DataFrame(
        [[0, 200, 1000, 100, 0, np.inf], [0, 20, 1000, 100, 0, 1.0]],
        index=index,
        columns=pd.MultiIndex.from_product([["feature"], columns]),
    )

    result = prepare_live_features(raw, bundle)

    assert result.index.get_level_values("instrument").tolist() == ["SH600000"]
    assert result.iloc[0]["F0"] == 0.0
