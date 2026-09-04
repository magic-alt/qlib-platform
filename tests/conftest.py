from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Callable

import pandas as pd
import pytest

from qlib_platform.artifacts import ArtifactType, PromotionStatus, stamp_artifact


@pytest.fixture
def governed_artifact(tmp_path: Path) -> Callable[..., pd.DataFrame]:
    def build(
        frame: pd.DataFrame,
        artifact_type: ArtifactType,
        *,
        status: PromotionStatus = PromotionStatus.PROMOTED,
        complete_lineage: bool = True,
        risk: dict[str, object] | None = None,
    ) -> pd.DataFrame:
        manifest = tmp_path / f"{artifact_type.value.lower()}_{status.value.lower()}_manifest.json"
        portfolio = {
            "top_n": 2,
            "min_score": None,
            "weighting": "score_vol",
            "max_position": 0.5,
            "max_exposure": 0.8,
            "max_group_exposure": 0.8,
            "max_turnover": None,
            "min_position": 0.002,
            "volatility_floor": 0.01,
        }
        portfolio_hash = hashlib.sha256(
            json.dumps(portfolio, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        manifest.write_text(
            json.dumps(
                {
                    "schemaVersion": "2.0",
                    "externalRunId": "run-1",
                    "model": {"fingerprint": "model-1"},
                    "dataset": {"fingerprint": "dataset-1"},
                    "lineage": {"lineageId": "lineage-1", "complete": complete_lineage},
                    "promotion": {"status": status.value, "decision": status.value},
                    "portfolioPolicySha256": portfolio_hash,
                    "canonicalConfig": {
                        "strategy": {
                            "topk": 2,
                            "n_drop": 1,
                            "hold_thresh": 5,
                            "only_tradable": True,
                            "forbid_all_trade_at_limit": True,
                            "risk_degree": 0.95,
                        },
                        "portfolio": portfolio,
                    },
                }
            ),
            encoding="utf-8",
        )
        return stamp_artifact(
            frame,
            artifact_type,
            promotion_status=status,
            run_id="run-1",
            model_id="model-1",
            dataset_id="dataset-1",
            lineage_id="lineage-1",
            manifest_path=manifest,
            portfolio_policy_sha256=(
                portfolio_hash if artifact_type is ArtifactType.TARGET_PORTFOLIO else None
            ),
        )

    return build
