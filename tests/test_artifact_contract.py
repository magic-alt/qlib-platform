from __future__ import annotations

import pandas as pd
import pytest

from tushare_qlib.artifacts import (
    ArtifactContractError,
    ArtifactType,
    PromotionStatus,
    validate_artifact,
)
from tushare_qlib.execution import ExecutionPolicy, build_orders, build_topk_orders


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
