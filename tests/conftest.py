from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import pandas as pd
import pytest

from tushare_qlib.artifacts import ArtifactType, PromotionStatus, stamp_artifact


@pytest.fixture
def governed_artifact(tmp_path: Path) -> Callable[..., pd.DataFrame]:
    def build(
        frame: pd.DataFrame,
        artifact_type: ArtifactType,
        *,
        status: PromotionStatus = PromotionStatus.PROMOTED,
        complete_lineage: bool = True,
    ) -> pd.DataFrame:
        manifest = tmp_path / f"{artifact_type.value.lower()}_{status.value.lower()}_manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "schemaVersion": "2.0",
                    "externalRunId": "run-1",
                    "model": {"fingerprint": "model-1"},
                    "dataset": {"fingerprint": "dataset-1"},
                    "lineage": {"lineageId": "lineage-1", "complete": complete_lineage},
                    "promotion": {"status": status.value, "decision": status.value},
                    "canonicalConfig": {
                        "strategy": {
                            "topk": 2,
                            "n_drop": 1,
                            "hold_thresh": 5,
                            "only_tradable": True,
                            "forbid_all_trade_at_limit": True,
                            "risk_degree": 0.95,
                        },
                        "execution": {
                            "board_lot": 100,
                            "max_participation_rate": 0.05,
                            "commission_rate": 0.00025,
                            "min_commission": 5.0,
                            "stamp_duty_sell": 0.0005,
                            "transfer_fee_rate": 0.00001,
                            "price_buffer_buy": 0.002,
                            "price_buffer_sell": 0.002,
                            "block_limit_up_buy": True,
                            "block_limit_down_sell": True,
                        },
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
        )

    return build
