from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from tushare_qlib.artifacts import (
    ArtifactContractError,
    ArtifactType,
    PromotionStatus,
    validate_artifact,
)
from tushare_qlib.execution import ExecutionPolicy, build_orders, build_topk_orders
from tushare_qlib.lean_bridge import export_lean_targets


def _positions() -> pd.DataFrame:
    return pd.DataFrame(
        {"instrument": ["SH600000"], "quantity": [0], "available_quantity": [0], "holding_days": [0]}
    )


def _quotes() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "instrument": ["SH600000"],
            "price": [10.0],
            "paused": [0],
            "is_limit_up": [0],
            "is_limit_down": [0],
            "adv20_volume": [100_000],
        }
    )


def test_legacy_target_artifact_fails_closed():
    legacy = pd.DataFrame({"instrument": ["SH600000"], "target_weight": [0.5]})
    with pytest.raises(ArtifactContractError, match="legacy or incomplete"):
        build_orders(
            legacy,
            _positions(),
            _quotes(),
            trade_date="2026-08-11",
            portfolio_value=100_000,
            cash=100_000,
        )


def test_model_topk_cannot_bypass_stateful_strategy(governed_artifact):
    topk = governed_artifact(
        pd.DataFrame({"instrument": ["SH600000"], "score": [1.0]}), ArtifactType.MODEL_TOPK
    )
    with pytest.raises(ArtifactContractError, match="cannot be used as MODEL_SCORE"):
        build_topk_orders(
            topk,
            _positions(),
            _quotes(),
            signal_date="2026-08-10",
            trade_date="2026-08-11",
            cash=100_000,
        )


def test_rejected_model_cannot_generate_orders(governed_artifact):
    rejected = governed_artifact(
        pd.DataFrame({"instrument": ["SH600000"], "score": [1.0]}),
        ArtifactType.MODEL_SCORE,
        status=PromotionStatus.REJECTED,
    )
    with pytest.raises(ArtifactContractError, match="only PROMOTED"):
        build_topk_orders(
            rejected,
            _positions(),
            _quotes(),
            signal_date="2026-08-10",
            trade_date="2026-08-11",
            cash=100_000,
        )


def test_dirty_research_candidate_cannot_enter_execution(governed_artifact):
    candidate = governed_artifact(
        pd.DataFrame({"instrument": ["SH600000"], "score": [1.0]}),
        ArtifactType.MODEL_SCORE,
        status=PromotionStatus.CANDIDATE,
        complete_lineage=False,
    )

    with pytest.raises(ArtifactContractError, match="only PROMOTED"):
        build_topk_orders(
            candidate,
            _positions(),
            _quotes(),
            signal_date="2026-08-10",
            trade_date="2026-08-11",
            cash=100_000,
        )


def test_incomplete_lineage_cannot_generate_orders(governed_artifact):
    incomplete = governed_artifact(
        pd.DataFrame({"instrument": ["SH600000"], "score": [1.0]}),
        ArtifactType.MODEL_SCORE,
        complete_lineage=False,
    )
    with pytest.raises(ArtifactContractError, match="lineage is missing or incomplete"):
        build_topk_orders(
            incomplete,
            _positions(),
            _quotes(),
            signal_date="2026-08-10",
            trade_date="2026-08-11",
            cash=100_000,
        )


def test_old_schema_cannot_generate_orders(governed_artifact):
    old = governed_artifact(
        pd.DataFrame({"instrument": ["SH600000"], "score": [1.0]}), ArtifactType.MODEL_SCORE
    )
    old["schema_version"] = "1.0"
    with pytest.raises(ArtifactContractError, match="unsupported artifact schema"):
        build_topk_orders(
            old,
            _positions(),
            _quotes(),
            signal_date="2026-08-10",
            trade_date="2026-08-11",
            cash=100_000,
        )


def test_runtime_execution_policy_cannot_drift_from_release(governed_artifact):
    target = governed_artifact(
        pd.DataFrame({"instrument": ["SH600000"], "target_weight": [0.5]}),
        ArtifactType.TARGET_PORTFOLIO,
    )
    with pytest.raises(ArtifactContractError, match="does not match"):
        build_orders(
            target,
            _positions(),
            _quotes(),
            trade_date="2026-08-11",
            portfolio_value=100_000,
            cash=100_000,
            policy=ExecutionPolicy(commission_rate=0.01),
        )


def test_modified_artifact_payload_fails_checksum(governed_artifact):
    target = governed_artifact(
        pd.DataFrame({"instrument": ["SH600000"], "target_weight": [0.5]}),
        ArtifactType.TARGET_PORTFOLIO,
    )
    target.loc[0, "target_weight"] = 0.9
    with pytest.raises(ArtifactContractError, match="checksum mismatch"):
        build_orders(
            target,
            _positions(),
            _quotes(),
            trade_date="2026-08-11",
            portfolio_value=100_000,
            cash=100_000,
        )


def test_parquet_round_trip_preserves_artifact_checksum(tmp_path, governed_artifact):
    score = governed_artifact(
        pd.DataFrame({"instrument": ["SH600000", "SZ000001"], "score": [0.2, 0.1]}),
        ArtifactType.MODEL_SCORE,
    )
    path = tmp_path / "score.parquet"
    score.to_parquet(path, index=False)

    metadata = validate_artifact(pd.read_parquet(path), ArtifactType.MODEL_SCORE)

    assert metadata["payload_sha256"] == score["payload_sha256"].iloc[0]


def test_build_orders_rejects_target_portfolio_policy_hash_drift(governed_artifact):
    target = governed_artifact(
        pd.DataFrame({"instrument": ["SH600000"], "target_weight": [0.5]}),
        ArtifactType.TARGET_PORTFOLIO,
    )
    target["portfolio_policy_sha256"] = "drifted"

    with pytest.raises(ArtifactContractError, match="policy hash does not match"):
        build_orders(
            target,
            _positions(),
            _quotes(),
            trade_date="2026-08-11",
            portfolio_value=100_000,
            cash=100_000,
        )


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
