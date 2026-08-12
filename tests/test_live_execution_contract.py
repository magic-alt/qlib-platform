from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from tushare_qlib.artifact_resolver import ArtifactResolver, sha256_path
from tushare_qlib.artifacts import ArtifactType
from tushare_qlib.execution import build_topk_orders
from tushare_qlib.live_artifacts import payload_sha256, stamp_live_artifact


def test_schema_three_live_score_enters_stateful_execution(tmp_path: Path):
    signal_root = tmp_path / "signals" / "signal-1"
    signal_root.mkdir(parents=True)
    core = pd.DataFrame(
        {
            "signal_date": ["2026-08-10", "2026-08-10"],
            "trade_date": ["2026-08-11", "2026-08-11"],
            "instrument": ["SH600000", "SZ000001"],
            "score": [0.9, 0.1],
            "score_rank": [1, 2],
        }
    )
    canonical = {
        "strategy": {
            "topk": 1,
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
            "max_quote_age_seconds": 120,
            "max_position_age_seconds": 300,
        },
        "risk": {
            "max_gross_exposure": 1.0,
            "max_single_name": 1.0,
            "max_sector_exposure": 1.0,
            "max_daily_loss": 0.03,
            "kill_switch": False,
        },
    }
    attestation = signal_root / "attestation.json"
    attestation.write_text(
        json.dumps(
            {
                "signalId": "signal-1",
                "deploymentId": "model-1",
                "datasetSha256": "dataset-1",
                "signalSha256": payload_sha256(core),
                "canonicalConfig": canonical,
            }
        ),
        encoding="utf-8",
    )
    scores = stamp_live_artifact(
        core,
        ArtifactType.MODEL_SCORE,
        deployment_id="model-1",
        dataset_sha256="dataset-1",
        signal_id="signal-1",
        manifest_uri=ArtifactResolver.signal_uri("signal-1", "attestation.json"),
        manifest_sha256=sha256_path(attestation),
    )
    captured = datetime.now(timezone.utc).isoformat()
    positions = pd.DataFrame(
        {
            "instrument": ["SH600000"],
            "quantity": [0],
            "available_quantity": [0],
            "holding_days": [0],
            "as_of_trade_date": ["2026-08-11"],
            "snapshot_at_utc": [captured],
        }
    )
    quotes = pd.DataFrame(
        {
            "instrument": ["SH600000", "SZ000001"],
            "price": [10.0, 20.0],
            "paused": [0, 0],
            "is_limit_up": [0, 0],
            "is_limit_down": [0, 0],
            "adv20_volume": [1_000_000, 1_000_000],
            "as_of_trade_date": ["2026-08-11", "2026-08-11"],
            "snapshot_at_utc": [captured, captured],
        }
    )

    decision, orders, blocked = build_topk_orders(
        scores,
        positions,
        quotes,
        signal_date="2026-08-10",
        trade_date="2026-08-11",
        cash=100_000,
        daily_pnl_pct=0.0,
        artifact_resolver=ArtifactResolver(roots={"signal": tmp_path / "signals"}),
    )

    assert set(decision["schema_version"]) == {"3.0"}
    assert set(orders["schema_version"]) == {"3.0"}
    assert orders["side"].tolist() == ["BUY"]
    assert blocked.empty
