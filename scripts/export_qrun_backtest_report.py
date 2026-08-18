"""Convert a completed Qlib qrun artifact directory into an auditable report bundle."""

from __future__ import annotations

import argparse
import json
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd
from ruamel.yaml import YAML

from tushare_qlib.backtest_report import export_holding_snapshots, write_backtest_report
from tushare_qlib.settings import Paths, Settings


def _required_artifact(root: Path, relative_path: str) -> Path:
    path = root / relative_path
    if not path.is_file():
        raise FileNotFoundError(f"required Qlib artifact not found: {path}")
    return path


def _as_series(value: Any) -> pd.Series:
    if isinstance(value, pd.Series):
        return value
    if hasattr(value, "data") and hasattr(value, "index"):
        return pd.Series(value.data, index=list(value.index))
    if value is None:
        return pd.Series(dtype=float)
    return pd.Series(value)


def extract_trade_audit(indicator: Any) -> pd.DataFrame:
    """Turn Qlib's order-indicator history into a portable executed-order table."""

    rows: list[dict[str, object]] = []
    for trade_date, values in indicator.order_indicator_his.items():
        raw_fields = values.data if hasattr(values, "data") else values
        if not isinstance(raw_fields, Mapping):
            raise TypeError("Qlib order indicator must expose a mapping of order fields")
        fields = {name: _as_series(value) for name, value in raw_fields.items()}
        instruments = sorted({str(code) for series in fields.values() for code in series.index})
        for instrument in instruments:

            def get(name: str) -> float:
                value = fields.get(name, pd.Series(dtype=float)).get(instrument)
                return float(value) if pd.notna(value) else 0.0

            requested = abs(get("amount"))
            filled = abs(get("deal_amount"))
            if requested <= 0 and filled <= 0:
                continue
            direction = get("trade_dir")
            action = "BUY" if direction >= 0.5 else "SELL"
            fill_rate = get("ffr")
            status = "FILLED" if fill_rate >= 0.999 else "PARTIAL" if filled > 0 else "REJECTED"
            rows.append(
                {
                    "trade_date": pd.Timestamp(trade_date).normalize(),
                    "instrument": instrument,
                    "target_action": action,
                    "actual_action": action if filled > 0 else "HOLD",
                    "order_requested": True,
                    "requested_quantity": requested,
                    "filled_quantity": filled,
                    "filled_price": get("trade_price"),
                    "filled_value": abs(get("trade_value")),
                    "trade_cost": abs(get("trade_cost")),
                    "execution_status": status,
                    "action_reason": "QLIB_TOPK_DROPOUT_EXECUTED_ORDER",
                }
            )
    return pd.DataFrame(rows)


def attach_position_snapshots(audit: pd.DataFrame, positions: Mapping[Any, Any]) -> pd.DataFrame:
    """Attach Qlib pre/post-trade inventory to every executed order."""

    result = audit.copy()
    if result.empty:
        result["quantity_before"] = pd.Series(dtype=float)
        result["quantity_after"] = pd.Series(dtype=float)
        return result
    position_by_date = {pd.Timestamp(date).normalize(): position for date, position in positions.items()}
    dates = sorted(position_by_date)
    previous_by_date = {
        date: position_by_date[dates[index - 1]] if index else None for index, date in enumerate(dates)
    }

    def quantity(position: Any, instrument: str) -> float:
        if position is None or instrument not in position.get_stock_list():
            return 0.0
        return float(position.get_stock_amount(instrument))

    result["quantity_before"] = [
        quantity(previous_by_date.get(pd.Timestamp(row.trade_date).normalize()), str(row.instrument))
        for row in result.itertuples(index=False)
    ]
    result["quantity_after"] = [
        quantity(position_by_date.get(pd.Timestamp(row.trade_date).normalize()), str(row.instrument))
        for row in result.itertuples(index=False)
    ]
    return result


def _nested(mapping: Mapping[str, Any], *keys: str) -> Mapping[str, Any]:
    value: Any = mapping
    for key in keys:
        if not isinstance(value, Mapping):
            return {}
        value = value.get(key, {})
    return value if isinstance(value, Mapping) else {}


def _load_workflow_config(path: Path) -> dict[str, Any]:
    value = YAML(typ="safe", pure=True).load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("workflow configuration must be a mapping")
    return value


def _portfolio_config(workflow: Mapping[str, Any]) -> Mapping[str, Any]:
    records = _nested(workflow, "task").get("record", [])
    return next(
        (
            _nested(record, "kwargs", "config")
            for record in records
            if isinstance(record, Mapping)
            and str(record.get("class")) in {"PortAnaRecord", "ASharePortAnaRecord"}
        ),
        {},
    )


def _report_configuration(workflow: Mapping[str, Any]) -> dict[str, str]:
    portfolio = _portfolio_config(workflow)
    strategy = _nested(portfolio, "strategy", "kwargs")
    backtest = _nested(portfolio, "backtest")
    exchange = _nested(backtest, "exchange_kwargs")
    segments = _nested(workflow, "task", "dataset", "kwargs").get("segments", {})
    handler = _nested(workflow, "task", "dataset", "kwargs", "handler")
    train = segments.get("train", []) if isinstance(segments, Mapping) else []
    valid = segments.get("valid", []) if isinstance(segments, Mapping) else []
    test = segments.get("test", []) if isinstance(segments, Mapping) else []

    def interval(value: Any) -> str:
        if isinstance(value, (list, tuple)) and len(value) == 2:
            return f"{value[0]} 至 {value[1]}"
        return str(value)

    limit_threshold = exchange.get("limit_threshold")
    limit_text = (
        "使用数据集涨跌停标记" if isinstance(limit_threshold, (list, tuple)) else str(limit_threshold)
    )
    volume_threshold = exchange.get("volume_threshold")
    volume_text = (
        f"成交量约束: {volume_threshold[1]}"
        if isinstance(volume_threshold, (list, tuple)) and len(volume_threshold) > 1
        else str(volume_threshold)
    )
    return {
        "股票池": str(workflow.get("market", "unknown")),
        "特征": str(handler.get("class", "unknown")),
        "训练 / 验证 / 测试": f"{interval(train)} / {interval(valid)} / {interval(test)}",
        "组合规则": f"TopK={strategy.get('topk')}, 每日换出={strategy.get('n_drop')}, 最短持有={strategy.get('hold_thresh')}",
        "初始资金": f"{float(backtest.get('account', 0)):,.0f} CNY",
        "基准 / 成交价": f"{backtest.get('benchmark')} / {exchange.get('deal_price')}",
        "涨跌停 / 成交量": f"{limit_text} / {volume_text}",
        "交易单位": f"{exchange.get('trade_unit')} 股",
        "买入 / 卖出费率": f"{exchange.get('open_cost')} / {exchange.get('close_cost')}",
        "最低佣金": str(exchange.get("min_cost")),
    }


def export_report_bundle(
    artifact_dir: Path, workflow_path: Path, output_dir: Path, settings: Settings
) -> None:
    artifact_dir = artifact_dir.resolve()
    workflow = _load_workflow_config(workflow_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    report = pd.read_pickle(_required_artifact(artifact_dir, "portfolio_analysis/report_normal_1day.pkl"))
    positions = pd.read_pickle(
        _required_artifact(artifact_dir, "portfolio_analysis/positions_normal_1day.pkl")
    )
    indicator = pd.read_pickle(
        _required_artifact(artifact_dir, "portfolio_analysis/indicators_normal_1day_obj.pkl")
    )
    predictions = pd.read_pickle(_required_artifact(artifact_dir, "pred.pkl"))
    labels = pd.read_pickle(_required_artifact(artifact_dir, "label.pkl"))
    predictions.to_parquet(output_dir / "oos_predictions.parquet")
    labels.to_parquet(output_dir / "oos_labels.parquet")
    report.to_parquet(output_dir / "portfolio_report.parquet")
    holdings = export_holding_snapshots(positions)
    holdings.to_parquet(output_dir / "holdings.parquet", index=False)
    audit = attach_position_snapshots(extract_trade_audit(indicator), positions)
    audit.to_parquet(output_dir / "strategy_audit.parquet", index=False)
    workflow_copy = output_dir / "workflow_config.yaml"
    shutil.copy2(workflow_path, workflow_copy)

    portfolio_config = _portfolio_config(workflow)
    strategy = _nested(portfolio_config, "strategy", "kwargs")
    backtest = _nested(portfolio_config, "backtest")
    topk = int(strategy.get("topk", 0))
    latest_date = predictions.index.get_level_values("datetime").max()
    latest_scores = (
        predictions.xs(latest_date, level="datetime").iloc[:, 0].sort_values(ascending=False).head(topk)
    )
    risk_degree = float(strategy.get("risk_degree", 1.0))
    targets = [
        {"instrument": str(instrument), "targetWeight": risk_degree / topk, "score": float(score)}
        for instrument, score in latest_scores.items()
    ]
    try:
        import lightgbm
        import qlib

        versions = {"qlib": qlib.__version__, "lightgbm": lightgbm.__version__}
    except ImportError:
        versions = {}
    artifacts = [
        {"name": name, "localPath": str(output_dir / name)}
        for name in (
            "oos_predictions.parquet",
            "oos_labels.parquet",
            "portfolio_report.parquet",
            "holdings.parquet",
            "strategy_audit.parquet",
            "workflow_config.yaml",
        )
    ]
    manifest = {
        "schemaVersion": "1.1",
        "externalRunId": artifact_dir.parent.name,
        "runKind": "qlib_qrun_fixed_split",
        "model": {"name": "LightGBM Alpha158"},
        "runtime": {"modelFamily": "lightgbm", "resolvedDevice": "cpu", "versions": versions},
        "execution": {
            "benchmark": backtest.get("benchmark"),
            "dealPrice": _nested(backtest, "exchange_kwargs").get("deal_price"),
        },
        "reportConfiguration": _report_configuration(workflow),
        "artifacts": artifacts,
        "latestTargets": {"asOf": str(latest_date.date()), "targets": targets},
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_backtest_report(settings, output_dir)


def _settings_for_data_root(data_root: Path) -> Settings:
    root = data_root.expanduser().resolve()
    return Settings(
        config_path=Path.cwd() / "configs" / "pipeline.yaml",
        data={"project_root": str(root)},
        paths=Paths.from_root(root),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=root,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", required=True, type=Path, help="Qlib recorder artifacts directory")
    parser.add_argument("--workflow-config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--data-root", default=Path("data"), type=Path, help="project data root for names and factors"
    )
    args = parser.parse_args()
    settings = _settings_for_data_root(args.data_root)
    export_report_bundle(args.artifact_dir, args.workflow_config, args.output_dir, settings)


if __name__ == "__main__":
    main()
