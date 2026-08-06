from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .client import TushareClient
from .settings import Settings
from .store import PartitionStore

_AUTO_BENCHMARK_CANDIDATES = ("SH000300", "SH000905", "SZ399001", "SZ399006", "SZ000001")


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
    return (
        (dates[0].strftime("%Y-%m-%d"), dates[train_end_idx].strftime("%Y-%m-%d")),
        (dates[train_end_idx + 1].strftime("%Y-%m-%d"), dates[valid_end_idx].strftime("%Y-%m-%d")),
        (dates[valid_end_idx + 1].strftime("%Y-%m-%d"), dates[-1].strftime("%Y-%m-%d")),
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


def _fetch_index_benchmark_series(
    code: str, start_time: str, end_time: str, token: str | None, calendar: pd.DatetimeIndex
) -> pd.Series | None:
    ts_code = _to_tushare_index_code(code)
    if not ts_code or not token:
        return None
    result = TushareClient(token).fetch(
        "index_daily",
        required=False,
        ts_code=ts_code,
        fields="trade_date,close",
        start_date=pd.Timestamp(start_time).strftime("%Y%m%d"),
        end_date=pd.Timestamp(end_time).strftime("%Y%m%d"),
    )
    frame = result.data
    if frame.empty or not {"trade_date", "close"}.issubset(frame.columns):
        return None
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], format="%Y%m%d", errors="coerce")
    close = frame.dropna(subset=["trade_date"]).set_index("trade_date")["close"].astype(float).sort_index()
    return close.pct_change().reindex(calendar).fillna(0.0).rename("benchmark")


def _resolve_benchmark(settings: Settings, benchmark: str | None, start_time: str, end_time: str) -> Any:
    from qlib.data import D

    calendar = pd.DatetimeIndex(D.calendar(start_time=start_time, end_time=end_time, freq="day"))
    if calendar.empty:
        raise RuntimeError("Qlib calendar is empty; benchmark cannot be resolved")
    candidates: list[str] = []
    for item in ([benchmark] if benchmark else []) + list(_AUTO_BENCHMARK_CANDIDATES):
        if item and item not in candidates:
            candidates.append(item)
    try:
        available = set(D.list_instruments({"market": "all", "filter_pipe": []}, start_time=start_time, end_time=end_time, as_list=True))
    except Exception:
        available = set()
    for candidate in candidates:
        normalized = _normalize_stock_code_for_qlib(candidate)
        if normalized in available:
            return normalized
        series = _fetch_index_benchmark_series(candidate, start_time, end_time, settings.tushare_token, calendar)
        if series is not None and not series.empty:
            return series
    research = settings.data.get("research", {})
    allow_zero = bool(research.get("allow_zero_benchmark", False)) if isinstance(research, dict) else False
    if allow_zero:
        return pd.Series(0.0, index=calendar, name="benchmark")
    raise RuntimeError(
        f"No benchmark resolved from {candidates}. Zero-return fallback is disabled because it invalidates excess-return metrics."
    )


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
            {"class": "ProcessInf"},
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
        benchmark_cfg = _resolve_benchmark(settings, benchmark, test[0], test[1])
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
                    "start_time": test[0],
                    "end_time": test[1],
                    "account": int(research.get("backtest_account", 100_000_000)),
                    "benchmark": benchmark_cfg,
                    "exchange_kwargs": {
                        "limit_threshold": float(research.get("limit_threshold", 0.095)),
                        "deal_price": str(research.get("deal_price", "open")),
                        "open_cost": float(research.get("open_cost", 0.00035)),
                        "close_cost": float(research.get("close_cost", 0.00085)),
                        "min_cost": float(research.get("min_cost", 5)),
                    },
                },
            },
        ).generate()

        pred = model.predict(dataset, segment="test")
        score = pred.iloc[:, 0] if isinstance(pred, pd.DataFrame) else pred
        latest = pd.Timestamp(score.index.get_level_values("datetime").max())
        selected = score.xs(latest, level="datetime").sort_values(ascending=False).head(topn)
        instruments = selected.index.astype(str).tolist()
        volatility = _selection_volatility(instruments, latest)
        model_id = str(getattr(recorder, "id", "unversioned"))
        output = selected.rename("score").reset_index().rename(columns={selected.index.name or "index": "instrument"})
        output["volatility"] = output["instrument"].map(volatility)
        output.insert(0, "trade_date", _next_trade_date(settings, latest))
        output.insert(0, "signal_date", latest.strftime("%Y-%m-%d"))
        output["model_id"] = model_id
        output["dataset_id"] = _dataset_id(settings)
        payload = f"{model_id}|{_dataset_id(settings)}|{latest.isoformat()}".encode()
        output["signal_id"] = hashlib.sha256(payload).hexdigest()[:24]
        path = settings.paths.output / f"selection_{latest:%Y%m%d}.csv"
        output.to_csv(path, index=False, encoding="utf-8-sig")
        return path
