from pathlib import Path

import pandas as pd
import pytest

from tushare_qlib.artifacts import ArtifactContractError, ArtifactType
from tushare_qlib.trade_plan import build_trade_plan


def test_trade_plan_uses_next_official_open_day(tmp_path: Path, monkeypatch, governed_artifact):
    monkeypatch.setattr(pd, "read_parquet", lambda path: pd.read_pickle(path))
    data = tmp_path / "data"
    output = data / "output"
    metadata = data / "metadata"
    output.mkdir(parents=True)
    metadata.mkdir(parents=True)
    pd.DataFrame(
        {
            "cal_date": pd.to_datetime(["2026-08-06", "2026-08-07", "2026-08-08", "2026-08-10"]),
            "is_open": [1, 1, 0, 1],
        }
    ).to_pickle(metadata / "trade_calendar.parquet")
    selection = governed_artifact(
        pd.DataFrame(
            {
                "signal_date": ["2026-08-07", "2026-08-07"],
                "instrument": ["SH600000", "SZ000001"],
                "score": [1.0, 0.5],
                "volatility": [0.02, 0.03],
            }
        ),
        ArtifactType.MODEL_TOPK,
    )
    selection_path = output / "selection_20260807.csv"
    selection.to_csv(selection_path, index=False)
    config = tmp_path / "configs" / "target_portfolio.yaml"
    config.parent.mkdir()
    config.write_text(
        """target_portfolio:\n  selection_dir: ./data/output\n  output_dir: ./data/output\n  calendar_path: ./data/metadata/trade_calendar.parquet\n""",
        encoding="utf-8",
    )
    path, plan = build_trade_plan(config_path=config, selection_file=selection_path)
    assert path.exists()
    assert set(plan["trade_date"]) == {"2026-08-10"}
    assert set(plan["artifact_type"]) == {ArtifactType.STRATEGY_DECISION.value}
    assert "ORDER_INTENT" not in set(plan["artifact_type"])
    targets = pd.read_csv(output / "target_portfolio_20260810.csv")
    assert set(targets["artifact_type"]) == {ArtifactType.TARGET_PORTFOLIO.value}
    assert targets["portfolio_policy_sha256"].nunique() == 1


def test_trade_plan_rejects_portfolio_semantics_in_local_template(tmp_path: Path, governed_artifact):
    selection = governed_artifact(
        pd.DataFrame(
            {
                "signal_date": ["2026-08-07"],
                "instrument": ["SH600000"],
                "score": [1.0],
                "volatility": [0.02],
            }
        ),
        ArtifactType.MODEL_TOPK,
    )
    selection_path = tmp_path / "selection_20260807.csv"
    selection.to_csv(selection_path, index=False)
    config = tmp_path / "configs" / "target_portfolio.yaml"
    config.parent.mkdir()
    config.write_text("target_portfolio:\n  portfolio:\n    top_n: 99\n", encoding="utf-8")

    with pytest.raises(ArtifactContractError, match="cannot define portfolio semantics"):
        build_trade_plan(config_path=config, selection_file=selection_path)
