from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .settings import Settings
from .store import PartitionStore

_DEFAULT_BENCHMARK = "SH000300"


def _configure_mlflow_tracking(settings: Settings) -> None:
    if "MLFLOW_TRACKING_URI" not in os.environ:
        db = settings.paths.state / "mlflow.db"
        os.environ["MLFLOW_TRACKING_URI"] = f"sqlite:///{db.resolve()}"
    if "MLFLOW_DEFAULT_ARTIFACT_ROOT" not in os.environ:
        os.environ["MLFLOW_DEFAULT_ARTIFACT_ROOT"] = str((settings.paths.models / "mlruns").resolve())


def _default_splits_from_data(settings: Settings) -> tuple[tuple[str, str], tuple[str, str], tuple[str, str]]:
    dates = pd.to_datetime(PartitionStore(settings.paths.raw).list_dates("daily"), format="%Y%m%d", errors="coerce")
    dates = pd.Index(sorted(d for d in dates if pd.notna(d)))
    research = settings.data.get("research", {})
    min_history = int(research.get("min_history_days", 756)) if isinstance(research, dict) else 756
    if len(dates) < min_history:
        raise ValueError(
            f"Commercial research gate requires at least {min_history} trading days; detected {len(dates)}. "
            "Pass explicit windows only for a labelled smoke test, never for model promotion."
        )
    n = len(dates)
    train_end_idx = (n * 6) // 10 - 1
    valid_end_idx = (n * 8) // 10 - 1
    # Test ends one day before the last calendar entry so the backtest
    # strategy can peek at the next trading day for its final step.
    test_end_idx = n - 2
    return (
        (dates[0].strftime("%Y-%m-%d"), dates[train_end_idx].strftime("%Y-%m-%d")),
        (dates[train_end_idx + 1].strftime("%Y-%m-%d"), dates[valid_end_idx].strftime("%Y-%m-%d")),
        (dates[valid_end_idx + 1].strftime("%Y-%m-%d"), dates[test_end_idx].strftime("%Y-%m-%d")),
    )


def _official_calendar(settings: Settings) -> pd.DatetimeIndex:
    path = settings.paths.metadata / "trade_calendar.parquet"
    if not path.exists():
        raise FileNotFoundError(f"official trading calendar is required: {path}")
    cal = pd.read_parquet(path)
    dates = pd.to_datetime(cal.loc[pd.to_numeric(cal["is_open"], errors="coerce") == 1, "cal_date"], errors="coerce")
    return pd.DatetimeIndex(dates.dropna().sort_values().unique())


def _next_trade_date(settings: Settings, signal_date: str | pd.Timestamp) -> str:
    signal = pd.Timestamp(signal_date).normalize()
    future = _official_calendar(settings)
    future = future[future > signal]
    if len(future) == 0:
        raise ValueError(f"official calendar has no open day after {signal.date()}")
    return future[0].strftime("%Y-%m-%d")


def _normalize_stock_code_for_qlib(code: str) -> str:
    value = code.strip().upper()
    match = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ)", value)
    return f"{match.group(2)}{match.group(1)}" if match else value


def _to_tushare_index_code(code: str) -> str | None:
    value = code.strip().upper()
    match = re.fullmatch(r"(SH|SZ|BJ)(\d{6})", value)
    if match:
        return f"{match.group(2)}.{match.group(1)}"
    return value if re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", value) else None


def _load_local_benchmark_series(settings: Settings, code: str, calendar: pd.DatetimeIndex) -> pd.Series:
    path = settings.paths.metadata / "benchmarks" / f"{code.upper()}.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"Local benchmark is required: {path}. Run `tq --config {settings.config_path} sync-benchmark`."
        )
    frame = pd.read_parquet(path)
    required = {"trade_date", "close"}
    if not required.issubset(frame.columns):
        raise ValueError(f"benchmark file missing columns: {sorted(required - set(frame.columns))}")
    dates = pd.to_datetime(frame["trade_date"], errors="coerce")
    close = pd.Series(pd.to_numeric(frame["close"], errors="coerce").to_numpy(), index=dates).dropna().sort_index()
    if close.index.duplicated().any():
        raise ValueError(f"duplicate benchmark dates in {path}")
    selected = close.reindex(calendar)
    if selected.isna().any():
        missing = selected[selected.isna()].index[:5].strftime("%Y-%m-%d").tolist()
        raise ValueError(f"benchmark does not cover backtest calendar; missing={missing}")
    return selected.pct_change().fillna(0.0).rename("benchmark")


def _resolve_benchmark(settings: Settings, benchmark: str | None, start_time: str, end_time: str) -> Any:
    from qlib.data import D

    calendar = pd.DatetimeIndex(D.calendar(start_time=start_time, end_time=end_time, freq="day"))
    if calendar.empty:
        raise RuntimeError("Qlib calendar is empty; benchmark cannot be resolved")
    code = _normalize_stock_code_for_qlib(benchmark or str(settings.data.get("research", {}).get("benchmark", _DEFAULT_BENCHMARK)))
    if code != _DEFAULT_BENCHMARK:
        raise ValueError(f"Only the certified index benchmark {_DEFAULT_BENCHMARK} is accepted, got {code}")
    return _load_local_benchmark_series(settings, code, calendar)


def build_dataset(
    *,
    train: tuple[str, str],
    valid: tuple[str, str],
    test: tuple[str, str],
    universe: dict[str, object] | None = None,
) -> Any:
    from qlib.data.dataset import DatasetH

    from .custom_handler import TushareAlpha158Daily

    universe = universe or {}
    handler = TushareAlpha158Daily(
        instruments="all",
        start_time=train[0],
        end_time=test[1],
        fit_start_time=train[0],
        fit_end_time=train[1],
        shared_processors=[
            {
                "class": "AshareUniverseFilter",
                "module_path": "tushare_qlib.processors",
                "kwargs": {
                    "min_listed_days": int(universe.get("min_listed_days", 120)),
                    "min_circ_mv_yuan": float(universe.get("min_circ_mv_yuan", 2_000_000_000)),
                    "min_money_20d_yuan": float(universe.get("min_money_20d_yuan", 20_000_000)),
                    "exclude_st": bool(universe.get("exclude_st", True)),
                    "allow_unknown_st": bool(universe.get("allow_unknown_st", False)),
                },
            }
        ],
        infer_processors=[
            {
                "class": "ProcessInfSingleThread",
                "module_path": "tushare_qlib.processors",
                "kwargs": {"n_jobs": 1},
            },
            {"class": "RobustZScoreNorm", "kwargs": {"fields_group": "feature", "clip_outlier": True}},
            {"class": "Fillna", "kwargs": {"fields_group": "feature"}},
        ],
        learn_processors=[
            {"class": "DropnaLabel"},
            {"class": "CSRankNorm", "kwargs": {"fields_group": "label"}},
        ],
    )
    return DatasetH(handler=handler, segments={"train": train, "valid": valid, "test": test})


def _dataset_id(settings: Settings) -> str:
    manifest = settings.qlib_data_uri / "dataset_manifest.json"
    if manifest.exists():
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        return str(payload.get("sha256", payload.get("dataset_id", "unversioned")))
    return "unversioned"


def _selection_volatility(instruments: list[str], latest: pd.Timestamp) -> pd.Series:
    from qlib.data import D

    start = latest - pd.Timedelta(days=60)
    prices = D.features(instruments, ["$close"], start_time=start, end_time=latest, freq="day")
    if prices.empty:
        return pd.Series(index=instruments, data=np.nan)
    close = prices.iloc[:, 0].unstack("instrument") if prices.index.names[0] == "datetime" else prices.iloc[:, 0].unstack("datetime").T
    return close.pct_change().tail(20).std().reindex(instruments)


def train_backtest_select(
    settings: Settings,
    *,
    train: tuple[str, str] | None = None,
    valid: tuple[str, str] | None = None,
    test: tuple[str, str] | None = None,
    benchmark: str | None = None,
    topn: int = 30,
    experiment_name: str = "tushare_alpha158_lgb",
    run_kind: str = "fixed_split",
) -> Path:
    try:
        import qlib
        from qlib.constant import REG_CN
        from qlib.contrib.model.gbdt import LGBModel
        from qlib.workflow import R
        from qlib.workflow.record_temp import PortAnaRecord, SigAnaRecord, SignalRecord
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("Install the qlib optional dependencies: pip install -e '.[qlib]'") from exc

    _configure_mlflow_tracking(settings)
    qlib.init(provider_uri=str(settings.qlib_data_uri), region=REG_CN, expression_cache=None, dataset_cache=None)
    if train is None or valid is None or test is None:
        train, valid, test = _default_splits_from_data(settings)
    if not (pd.Timestamp(train[1]) < pd.Timestamp(valid[0]) <= pd.Timestamp(valid[1]) < pd.Timestamp(test[0])):
        raise ValueError("train/valid/test windows must be strictly chronological and non-overlapping")

    research = settings.data.get("research", {}) if isinstance(settings.data.get("research", {}), dict) else {}
    seed = int(research.get("random_seed", 42))
    np.random.seed(seed)
    dataset = build_dataset(train=train, valid=valid, test=test, universe=settings.data.get("universe", {}))
    model = LGBModel(
        loss="mse",
        learning_rate=0.03,
        num_leaves=63,
        max_depth=8,
        colsample_bytree=0.8,
        subsample=0.8,
        lambda_l1=10.0,
        lambda_l2=50.0,
        num_threads=int(research.get("num_threads", 8)),
        early_stopping_rounds=100,
        num_boost_round=2000,
        seed=seed,
        feature_fraction_seed=seed,
        bagging_seed=seed,
        data_random_seed=seed,
    )

    tracking_uri = os.environ["MLFLOW_TRACKING_URI"]
    with R.start(experiment_name=experiment_name, uri=tracking_uri):
        model.fit(dataset)
        recorder = R.get_recorder()
        R.save_objects(**{"params.pkl": model, "dataset": dataset})
        SignalRecord(model=model, dataset=dataset, recorder=recorder).generate()
        SigAnaRecord(recorder=recorder, ana_long_short=True, ann_scaler=252).generate()
        pred = recorder.load_object("pred.pkl")
        pred_dates = pred.index.get_level_values("datetime")
        oos_start = pd.Timestamp(pred_dates.min()).strftime("%Y-%m-%d")
        oos_end = pd.Timestamp(pred_dates.max()).strftime("%Y-%m-%d")
        benchmark_cfg = _resolve_benchmark(settings, benchmark, oos_start, oos_end)
        PortAnaRecord(
            recorder=recorder,
            config={
                "strategy": {
                    "class": "TopkDropoutStrategy",
                    "module_path": "qlib.contrib.strategy",
                    "kwargs": {
                        "signal": "<PRED>",
                        "topk": topn,
                        "n_drop": max(1, topn // 6),
                        "hold_thresh": 5,
                        "only_tradable": True,
                        "forbid_all_trade_at_limit": True,
                    },
                },
                "executor": {
                    "class": "SimulatorExecutor",
                    "module_path": "qlib.backtest.executor",
                    "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True},
                },
                "backtest": {
                    "start_time": oos_start,
                    "end_time": oos_end,
                    "account": int(research.get("backtest_account", 100_000_000)),
                    "benchmark": benchmark_cfg,
                    "exchange_kwargs": {
                        "limit_threshold": ("$is_limit_up > 0", "$is_limit_down > 0"),
                        "deal_price": str(research.get("deal_price", "open")),
                        "volume_threshold": ("current", f"$volume * {float(research.get('max_participation_rate', 0.05))}"),
                        "trade_unit": int(research.get("trade_unit", 100)),
                        "open_cost": float(research.get("open_cost", 0.00035)),
                        "close_cost": float(research.get("close_cost", 0.00085)),
                        "min_cost": float(research.get("min_cost", 5)),
                    },
                },
            },
        ).generate()

        score = pred.iloc[:, 0] if isinstance(pred, pd.DataFrame) else pred
        latest = pd.Timestamp(score.index.get_level_values("datetime").max())
        selected = score.xs(latest, level="datetime").sort_values(ascending=False).head(topn)
        instruments = selected.index.astype(str).tolist()
        volatility = _selection_volatility(instruments, latest)
        model_id = str(getattr(recorder, "id", "unversioned"))
        output = selected.rename("score").reset_index().rename(columns={selected.index.name or "index": "instrument"})
        output["volatility"] = output["instrument"].map(volatility)
        output["target_weight"] = 1.0 / len(output) if len(output) else 0.0
        output.insert(0, "trade_date", _next_trade_date(settings, latest))
        output.insert(0, "signal_date", latest.strftime("%Y-%m-%d"))
        output["model_id"] = model_id
        output["dataset_id"] = _dataset_id(settings)
        payload = f"{model_id}|{_dataset_id(settings)}|{latest.isoformat()}".encode()
        output["signal_id"] = hashlib.sha256(payload).hexdigest()[:24]
        path = settings.paths.output / f"selection_{latest:%Y%m%d}.csv"
        output.to_csv(path, index=False, encoding="utf-8-sig")
        report = recorder.load_object("portfolio_analysis/report_normal_1day.pkl")
        artifact_dir = settings.paths.output / "research" / model_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        pred_path = artifact_dir / "oos_predictions.parquet"
        report_path = artifact_dir / "portfolio_report.parquet"
        pred.to_parquet(pred_path)
        report.to_parquet(report_path)
        metrics: dict[str, float] = {}
        for column in ("return", "bench", "cost"):
            if column in report:
                values = pd.to_numeric(report[column], errors="coerce").dropna()
                metrics[f"{column}Total"] = float((1.0 + values).prod() - 1.0)
        manifest = {
            "schemaVersion": "1.0",
            "externalRunId": model_id,
            "runKind": run_kind,
            "name": f"Qlib {run_kind} {oos_start}..{oos_end}",
            "dataset": {
                "fingerprint": _dataset_id(settings),
                "source": "lean_mysql" if not settings.uses_tushare_source() else "tushare",
                "universe": "CSI300" if not settings.uses_tushare_source() else "all",
                "startDate": train[0],
                "endDate": oos_end,
            },
            "model": {"name": "Alpha158-LGBM", "fingerprint": model_id},
            "folds": [{"key": run_kind, "train": list(train), "valid": list(valid), "test": [oos_start, oos_end]}],
            "execution": {
                "benchmark": benchmark or str(research.get("benchmark", _DEFAULT_BENCHMARK)),
                "signalLagDays": int(research.get("signal_lag_days", 1)),
                "dealPrice": str(research.get("deal_price", "open")),
                "tradeUnit": int(research.get("trade_unit", 100)),
                "maxParticipationRate": float(research.get("max_participation_rate", 0.05)),
            },
            "metrics": metrics,
            "artifacts": [
                {"name": pred_path.name, "localPath": str(pred_path), "rows": len(pred)},
                {"name": report_path.name, "localPath": str(report_path), "rows": len(report)},
            ],
            "latestTargets": {
                "signalDate": latest.strftime("%Y-%m-%d"),
                "tradeDate": _next_trade_date(settings, latest),
                "targets": [
                    {
                        "instrument": str(row.instrument),
                        "targetWeight": float(row.target_weight),
                        "score": float(row.score),
                    }
                    for row in output.itertuples(index=False)
                ],
            },
        }
        manifest_path = artifact_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
