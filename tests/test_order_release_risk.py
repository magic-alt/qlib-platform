from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

from tushare_qlib.artifacts import ArtifactType
from tushare_qlib.cli import main
from tushare_qlib.execution import build_topk_orders
from tushare_qlib.risk_engine import RiskLimitError


BASE_RISK = {
    "max_gross_exposure": 0.95,
    "max_single_name": 0.50,
    "max_sector_exposure": 0.60,
    "max_daily_loss": 0.03,
    "kill_switch": False,
}


@pytest.mark.parametrize(
    ("case", "weights", "sectors", "risk", "daily_pnl"),
    [
        ("kill", [0.10], ["bank"], {**BASE_RISK, "kill_switch": True}, 0.0),
        (
            "gross",
            [0.60, 0.50],
            ["bank", "tech"],
            {
                **BASE_RISK,
                "max_gross_exposure": 0.50,
                "max_single_name": 0.70,
                "max_sector_exposure": 1.0,
            },
            0.0,
        ),
        ("single", [0.20], ["bank"], {**BASE_RISK, "max_single_name": 0.10}, 0.0),
        ("sector", [0.20, 0.20], ["bank", "bank"], {**BASE_RISK, "max_sector_exposure": 0.30}, 0.0),
        ("daily_loss", [0.10], ["bank"], BASE_RISK, -0.04),
        ("missing_sector", [0.10, 0.10], None, BASE_RISK, 0.0),
        ("missing_daily_pnl", [0.10], ["bank"], BASE_RISK, float("nan")),
    ],
)
def test_build_orders_cli_never_writes_order_intent_when_hard_risk_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    governed_artifact,
    case: str,
    weights: list[float],
    sectors: list[str] | None,
    risk: dict[str, object],
    daily_pnl: float,
):
    instruments = [f"SH6000{index:02d}" for index in range(len(weights))]
    target_data: dict[str, object] = {"instrument": instruments, "target_weight": weights}
    if sectors is not None:
        target_data["sector"] = sectors
    targets = governed_artifact(
        pd.DataFrame(target_data),
        ArtifactType.TARGET_PORTFOLIO,
        risk=risk,
    )
    snapshot = pd.Timestamp.now(tz="UTC").isoformat()
    positions = pd.DataFrame(
        {
            "instrument": instruments,
            "quantity": [0] * len(weights),
            "available_quantity": [0] * len(weights),
            "as_of_trade_date": ["2026-08-11"] * len(weights),
            "snapshot_at_utc": [snapshot] * len(weights),
        }
    )
    quotes = pd.DataFrame(
        {
            "instrument": instruments,
            "price": [10.0] * len(weights),
            "paused": [0] * len(weights),
            "is_limit_up": [0] * len(weights),
            "is_limit_down": [0] * len(weights),
            "as_of_trade_date": ["2026-08-11"] * len(weights),
            "snapshot_at_utc": [snapshot] * len(weights),
        }
    )
    targets_path = tmp_path / f"targets-{case}.csv"
    positions_path = tmp_path / f"positions-{case}.csv"
    quotes_path = tmp_path / f"quotes-{case}.csv"
    targets.to_csv(targets_path, index=False)
    positions.to_csv(positions_path, index=False)
    quotes.to_csv(quotes_path, index=False)
    config = tmp_path / f"pipeline-{case}.yaml"
    config.write_text(
        f"project_root: {tmp_path / 'data'}\nqlib:\n  dataset_dir: {tmp_path / 'qlib'}\n",
        encoding="utf-8",
    )
    output = tmp_path / f"orders-{case}"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tq",
            "--config",
            str(config),
            "build-orders",
            str(targets_path),
            str(positions_path),
            str(quotes_path),
            "--trade-date",
            "2026-08-11",
            "--portfolio-value",
            "100000",
            "--cash",
            "100000",
            "--daily-pnl-pct",
            str(daily_pnl),
            "--output-dir",
            str(output),
        ],
    )

    with pytest.raises(RiskLimitError):
        main()

    assert not output.exists()


def test_topk_order_builder_cannot_bypass_manifest_kill_switch(governed_artifact):
    scores = governed_artifact(
        pd.DataFrame(
            {
                "instrument": ["SH600000"],
                "score": [1.0],
                "sector": ["bank"],
                "signal_date": ["2026-08-10"],
                "trade_date": ["2026-08-11"],
            }
        ),
        ArtifactType.MODEL_SCORE,
        risk={**BASE_RISK, "kill_switch": True},
    )
    snapshot = pd.Timestamp.now(tz="UTC").isoformat()
    positions = pd.DataFrame(
        {
            "instrument": ["SH600000"],
            "quantity": [0],
            "available_quantity": [0],
            "holding_days": [0],
            "as_of_trade_date": ["2026-08-11"],
            "snapshot_at_utc": [snapshot],
        }
    )
    quotes = pd.DataFrame(
        {
            "instrument": ["SH600000"],
            "price": [10.0],
            "paused": [0],
            "is_limit_up": [0],
            "is_limit_down": [0],
            "sector": ["bank"],
            "as_of_trade_date": ["2026-08-11"],
            "snapshot_at_utc": [snapshot],
        }
    )

    with pytest.raises(RiskLimitError, match="kill switch"):
        build_topk_orders(
            scores,
            positions,
            quotes,
            signal_date="2026-08-10",
            trade_date="2026-08-11",
            cash=100_000,
            daily_pnl_pct=0.0,
        )
