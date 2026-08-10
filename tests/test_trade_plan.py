from pathlib import Path

import pandas as pd

from tushare_qlib.artifacts import ArtifactType
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
    config = tmp_path / "configs" / "execution.yaml"
    config.parent.mkdir()
    config.write_text(
        """execution:\n  selection_dir: ./data/output\n  output_dir: ./data/output\n  calendar_path: ./data/metadata/trade_calendar.parquet\n  portfolio:\n    top_n: 2\n    weighting: score_vol\n    max_position: 0.5\n    max_exposure: 0.8\n    max_group_exposure: 0.8\n    max_turnover: null\n""",
        encoding="utf-8",
    )
    path, plan = build_trade_plan(config_path=config, selection_file=selection_path)
    assert path.exists()
    assert set(plan["trade_date"]) == {"2026-08-10"}
