from __future__ import annotations

import json

import pandas as pd

from qlib_platform.research.feature_store import materialize_feature_snapshot
from qlib_platform.settings import Paths, Settings


def _settings(tmp_path, *, label_horizon: int, model_profile: str) -> Settings:
    return Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={
            "experiment": {
                "alpha": {"pack": "alpha158_pit_v1"},
                "label": {"spec": f"return_{label_horizon}d_t1_v1"},
                "model": {"profile": model_profile},
            },
            "research": {
                "feature_store": {"enabled": True},
                "label_horizon_days": label_horizon,
            },
            "universe": {"instruments": "all"},
        },
        paths=Paths.from_root(tmp_path / "data"),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib",
    )


def test_feature_snapshot_is_reused_across_label_model_and_subwindow(tmp_path, monkeypatch):
    index = pd.MultiIndex.from_tuples(
        [
            (pd.Timestamp("2023-12-29"), "SH600000"),
            (pd.Timestamp("2024-01-02"), "SH600000"),
            (pd.Timestamp("2024-01-03"), "SH600000"),
        ],
        names=["datetime", "instrument"],
    )
    source = pd.DataFrame(
        [[1.0], [2.0], [3.0]],
        index=index,
        columns=pd.MultiIndex.from_tuples([("feature", "A")]),
    )
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        "qlib_platform.research.feature_store._raw_features",
        lambda *args: calls.append(args) or source,
    )

    first = materialize_feature_snapshot(
        _settings(tmp_path, label_horizon=5, model_profile="lightgbm_default_v1"),
        "2023-12-29",
        "2024-01-03",
    )
    second = materialize_feature_snapshot(
        _settings(tmp_path, label_horizon=10, model_profile="ridge_golden_v1"),
        "2024-01-02",
        "2024-01-03",
    )

    manifest = json.loads((first / "manifest.json").read_text(encoding="utf-8"))
    contract = manifest["contract"]
    assert first == second
    assert len(calls) == 1
    assert "requestedCoverage" not in contract
    assert "label" not in contract
    assert "model" not in contract
    assert manifest["featureRecipeId"].startswith("fr_")
    assert manifest["featureSnapshotId"].startswith("fs_")
