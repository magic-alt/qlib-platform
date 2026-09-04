from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from qlib_platform.artifacts import ArtifactContractError, ArtifactType, validate_artifact
from qlib_platform.lean_bridge import export_lean_targets


def test_parquet_round_trip_preserves_artifact_checksum(tmp_path, governed_artifact):
    score = governed_artifact(
        pd.DataFrame({"instrument": ["SH600000", "SZ000001"], "score": [0.2, 0.1]}),
        ArtifactType.MODEL_SCORE,
    )
    path = tmp_path / "score.parquet"
    score.to_parquet(path, index=False)

    metadata = validate_artifact(pd.read_parquet(path), ArtifactType.MODEL_SCORE)

    assert metadata["payload_sha256"] == score["payload_sha256"].iloc[0]


def test_target_portfolio_policy_hash_drift_fails_validation(governed_artifact):
    target = governed_artifact(
        pd.DataFrame({"instrument": ["SH600000"], "target_weight": [0.5]}),
        ArtifactType.TARGET_PORTFOLIO,
    )
    target["portfolio_policy_sha256"] = "drifted"

    with pytest.raises(ArtifactContractError, match="policy hash does not match"):
        validate_artifact(target, ArtifactType.TARGET_PORTFOLIO)


def test_lean_export_rejects_manifest_portfolio_policy_drift(tmp_path, governed_artifact):
    target = governed_artifact(
        pd.DataFrame({"instrument": ["SH600000"], "target_weight": [0.5]}),
        ArtifactType.TARGET_PORTFOLIO,
    )
    manifest_path = target["manifest_path"].iloc[0]
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    manifest["canonicalConfig"]["portfolio"]["top_n"] = 99
    Path(manifest_path).write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ArtifactContractError, match="hash does not match canonical config"):
        export_lean_targets(
            target,
            tmp_path,
            signal_date="2026-08-10",
            trade_date="2026-08-11",
            model_id="model-1",
            dataset_id="dataset-1",
        )
