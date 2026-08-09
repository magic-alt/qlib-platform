from __future__ import annotations

import json
import pickle
from pathlib import Path

import pandas as pd
from pypdf import PdfReader

from tushare_qlib.backtest_report import export_holding_snapshots, write_backtest_report
from tushare_qlib.settings import Paths, Settings


class _Position:
    def __init__(self, cash: float, stocks: dict[str, dict[str, float]]):
        self.cash = cash
        self.stocks = stocks

    def calculate_value(self) -> float:
        return self.cash + sum(item["amount"] * item["price"] for item in self.stocks.values())

    def get_cash(self) -> float:
        return self.cash

    def get_stock_list(self) -> list[str]:
        return list(self.stocks)

    def get_stock_amount(self, instrument: str) -> float:
        return self.stocks[instrument]["amount"]

    def get_stock_price(self, instrument: str) -> float:
        return self.stocks[instrument]["price"]

    def get_stock_weight(self, instrument: str) -> float:
        return self.stocks[instrument]["amount"] * self.stocks[instrument]["price"] / self.calculate_value()

    def get_stock_count(self, instrument: str, _: str) -> int:
        return int(self.stocks[instrument]["days"])


def _settings(tmp_path: Path) -> Settings:
    paths = Paths.from_root(tmp_path / "data")
    paths.mkdirs()
    pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "600000.SH"],
            "name": ["平安银行", "浦发银行"],
        }
    ).to_parquet(paths.metadata / "stock_master.parquet", index=False)
    return Settings(
        config_path=tmp_path / "configs" / "pipeline.yaml",
        data={"project_root": str(paths.root)},
        paths=paths,
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib",
    )


def _write_run(settings: Settings, *, holdings: bool = True) -> Path:
    run_dir = settings.paths.output / "research" / "run-1"
    run_dir.mkdir(parents=True)
    dates = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"])
    pd.DataFrame(
        {
            "account": [100_000.0, 102_000.0, 101_000.0],
            "return": [0.0, 0.02, -0.0098039216],
            "bench": [0.0, 0.01, -0.005],
            "cash": [100_000.0, 2_000.0, 4_000.0],
            "value": [0.0, 100_000.0, 97_000.0],
            "turnover": [0.0, 0.98, 0.1],
            "total_turnover": [0.0, 98_000.0, 108_000.0],
            "total_cost": [0.0, 35.0, 42.0],
        },
        index=dates,
    ).to_parquet(run_dir / "portfolio_report.parquet")
    pd.DataFrame(
        {
            "trade_date": [dates[1], dates[2]],
            "instrument": ["SZ000001", "SH600000"],
            "target_action": ["BUY", "SELL"],
            "actual_action": ["BUY", "SELL"],
            "order_requested": [True, True],
            "requested_quantity": [10_000.0, 8_000.0],
            "filled_quantity": [10_000.0, 8_000.0],
            "filled_price": [10.0, 12.0],
            "filled_value": [100_000.0, 96_000.0],
            "trade_cost": [35.0, 7.0],
            "execution_status": ["FILLED", "FILLED"],
            "action_reason": ["TOPK_FILL_OR_REPLACEMENT", "DROP_LOWEST_COMBINED_SCORE"],
        }
    ).to_parquet(run_dir / "strategy_audit.parquet", index=False)
    artifacts: list[dict[str, object]] = [
        {"name": "portfolio_report.parquet", "localPath": str(run_dir / "portfolio_report.parquet"), "rows": 3},
        {"name": "strategy_audit.parquet", "localPath": str(run_dir / "strategy_audit.parquet"), "rows": 2},
    ]
    if holdings:
        pd.DataFrame(
            {
                "trade_date": [dates[1], dates[2]],
                "instrument": ["SZ000001", "SH600000"],
                "quantity": [10_000.0, 8_000.0],
                "price": [10.0, 12.0],
                "market_value": [100_000.0, 96_000.0],
                "weight": [0.98, 0.95],
                "holding_days": [1, 2],
                "cash": [2_000.0, 4_000.0],
                "account": [102_000.0, 101_000.0],
            }
        ).to_parquet(run_dir / "holdings.parquet", index=False)
        artifacts.append({"name": "holdings.parquet", "localPath": str(run_dir / "holdings.parquet"), "rows": 2})
    manifest = {
        "schemaVersion": "1.1",
        "externalRunId": "run-1",
        "runKind": "fixed_split",
        "model": {"name": "Alpha158-LGBM"},
        "execution": {"benchmark": "SH000300", "dealPrice": "open"},
        "artifacts": artifacts,
        "latestTargets": {"targets": [{"instrument": "SZ000001", "targetWeight": 0.5, "score": 0.12}]},
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return run_dir


def test_report_writes_markdown_pdf_charts_and_transaction_appendix(tmp_path: Path):
    settings = _settings(tmp_path)
    run_dir = _write_run(settings)

    artifacts = write_backtest_report(settings, run_dir)

    assert artifacts.markdown_path.is_file()
    assert artifacts.pdf_path.is_file()
    assert {path.name for path in artifacts.assets_dir.glob("*.png")} == {
        "performance.png",
        "pnl_drawdown.png",
        "exposure_positions.png",
        "final_holdings.png",
        "trade_activity.png",
    }
    markdown = artifacts.markdown_path.read_text(encoding="utf-8")
    assert "期末持仓" in markdown
    assert "逐笔委托与成交" in markdown
    assert "平安银行 (SZ000001)" in markdown
    assert "浦发银行 (SH600000)" in markdown
    reader = PdfReader(str(artifacts.pdf_path))
    assert len(reader.pages) >= 7
    assert "回测报告" in reader.pages[0].extract_text()


def test_report_loads_legacy_mlflow_position_snapshot(tmp_path: Path):
    settings = _settings(tmp_path)
    run_dir = _write_run(settings, holdings=False)
    position_path = tmp_path / "mlruns" / "1" / "run-1" / "artifacts" / "portfolio_analysis" / "positions_normal_1day.pkl"
    position_path.parent.mkdir(parents=True)
    positions = {
        pd.Timestamp("2026-01-05"): _Position(100_000.0, {}),
        pd.Timestamp("2026-01-06"): _Position(2_000.0, {"SZ000001": {"amount": 10_000.0, "price": 10.0, "days": 1}}),
        pd.Timestamp("2026-01-07"): _Position(4_000.0, {"SH600000": {"amount": 8_000.0, "price": 12.0, "days": 2}}),
    }
    with position_path.open("wb") as handle:
        pickle.dump(positions, handle)

    artifacts = write_backtest_report(settings, run_dir)

    assert artifacts.pdf_path.is_file()
    snapshot = export_holding_snapshots(positions)
    assert snapshot["instrument"].tolist() == ["SZ000001", "SH600000"]
