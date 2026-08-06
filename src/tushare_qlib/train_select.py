from __future__ import annotations

import os
import re
from typing import Any
from pathlib import Path

import pandas as pd
import qlib
from qlib.constant import REG_CN
from qlib.contrib.model.gbdt import LGBModel
from qlib.data.dataset import DatasetH
from qlib.workflow import R
from qlib.workflow.record_temp import PortAnaRecord, SigAnaRecord, SignalRecord
from qlib.data import D

from .custom_handler import TushareAlpha158Daily
from .store import PartitionStore
from .settings import Settings
from .client import TushareClient


_AUTO_BENCHMARK_CANDIDATES = ("SH000300", "SH000905", "SZ399001", "SZ399006", "SZ000001")


def _configure_mlflow_tracking(settings: Settings) -> None:
    if "MLFLOW_TRACKING_URI" not in os.environ:
        tracking_db_path = settings.paths.root / ".mlflow" / "mlflow.db"
        tracking_db_path.parent.mkdir(parents=True, exist_ok=True)
        os.environ["MLFLOW_TRACKING_URI"] = f"sqlite:///{tracking_db_path.resolve()}"

    if "MLFLOW_DEFAULT_ARTIFACT_ROOT" not in os.environ:
        os.environ["MLFLOW_DEFAULT_ARTIFACT_ROOT"] = str((settings.paths.root / "mlruns").resolve())


def _get_mlflow_tracking_uri(settings: Settings) -> str:
    tracking_db_path = (settings.paths.root / ".mlflow" / "mlflow.db").resolve()
    tracking_db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{tracking_db_path}"


def _default_splits_from_data(settings: Settings) -> tuple[tuple[str, str], tuple[str, str], tuple[str, str]]:
    raw = PartitionStore(settings.paths.raw)
    dates = pd.to_datetime(raw.list_dates("daily"), format="%Y%m%d", errors="coerce")
    dates = pd.Index(sorted(d for d in dates if pd.notna(d)))
    if len(dates) < 3:
        raise ValueError(
            "Not enough trade dates available for train/valid/test split. "
            f"Detected {len(dates)} dated files in raw daily data. "
            "Run `tq --config configs/pipeline.yaml backfill` first."
        )
    n = len(dates)
    train_end_idx = max((n * 6) // 10 - 1, 0)
    valid_end_idx = max((n * 8) // 10 - 1, train_end_idx + 1)
    if valid_end_idx >= n - 1:
        valid_end_idx = n - 2
        train_end_idx = min(train_end_idx, valid_end_idx - 1)
    if train_end_idx >= valid_end_idx:
        raise ValueError(f"Unable to build non-empty windows from {n} trade dates.")

    train_start = dates[0].strftime("%Y-%m-%d")
    train_end = dates[train_end_idx].strftime("%Y-%m-%d")
    valid_start = dates[train_end_idx + 1].strftime("%Y-%m-%d")
    valid_end = dates[valid_end_idx].strftime("%Y-%m-%d")
    test_start = dates[valid_end_idx + 1].strftime("%Y-%m-%d")
    test_end = dates[-1].strftime("%Y-%m-%d")
    return (train_start, train_end), (valid_start, valid_end), (test_start, test_end)


def _resolve_trading_calendar(start_time: str, end_time: str, future: bool = False) -> pd.DatetimeIndex:
    try:
        cal = pd.DatetimeIndex(D.calendar(start_time=start_time, end_time=end_time, freq="day", future=future))
        if len(cal) == 0:
            raise ValueError("qlib trading calendar returned empty")
        return cal
    except Exception:
        return pd.date_range(pd.Timestamp(start_time), pd.Timestamp(end_time), freq="B")


def _build_zero_benchmark(start_time: str, end_time: str) -> pd.Series:
    cal = _resolve_trading_calendar(start_time, end_time)
    if len(cal) == 0:
        raise ValueError(f"benchmark requires at least one trading date between {start_time} and {end_time}")
    return pd.Series(0.0, index=cal, name="benchmark")


def _next_trading_day(end_time: str, lookback_start: str, lookahead_days: int = 14) -> str:
    end_ts = pd.Timestamp(end_time)
    probe_end = (end_ts + pd.Timedelta(days=lookahead_days)).strftime("%Y-%m-%d")
    cal = _resolve_trading_calendar(end_ts.strftime("%Y-%m-%d"), probe_end, future=True)
    future_days = cal[cal > end_ts]
    if len(future_days) == 0:
        cal_within_range = _resolve_trading_calendar(lookback_start, end_time)
        if len(cal_within_range) < 2:
            return end_time
        return cal_within_range[-2].strftime("%Y-%m-%d")
    return future_days[0].strftime("%Y-%m-%d")


def _normalize_stock_code_for_qlib(code: str) -> str:
    code = code.strip()
    m = re.fullmatch(r"(?i)^(\\d{6})\\.(SH|SZ|BJ)$", code)
    if m:
        return f"{m.group(2).upper()}{m.group(1)}"
    return code


def _to_tushare_index_code(code: str) -> str | None:
    normalized = code.strip().upper()
    m = re.fullmatch(r"(?i)^(SH|SZ|BJ)(\\d{6})$", normalized)
    if m:
        return f"{m.group(2)}.{m.group(1).upper()}"
    if re.fullmatch(r"\\d{6}\\.(SH|SZ|BJ)", normalized):
        return normalized
    return None


def _fetch_index_benchmark_series(code: str, start_time: str, end_time: str, token: str) -> pd.Series | None:
    ts_code = _to_tushare_index_code(code)
    if not ts_code:
        return None
    try:
        start = pd.Timestamp(start_time).strftime("%Y%m%d")
        end = pd.Timestamp(end_time).strftime("%Y%m%d")
    except Exception:
        start = str(start_time).replace("-", "")
        end = str(end_time).replace("-", "")
    try:
        client = TushareClient(token)
        index_df = client.call(
            "index_daily", required=False,
            ts_code=ts_code, fields="trade_date,close", start_date=start, end_date=end,
        )
    except Exception:
        return None
    if index_df.empty:
        return None
    if "trade_date" not in index_df.columns or "close" not in index_df.columns:
        return None
    index_df = index_df.copy()
    index_df["trade_date"] = pd.to_datetime(index_df["trade_date"], format="%Y%m%d", errors="coerce")
    index_df = index_df.dropna(subset=["trade_date"]).set_index("trade_date").sort_index()
    benchmark = index_df["close"].astype(float).pct_change().fillna(0.0)
    benchmark = benchmark.rename("benchmark")
    return benchmark.reindex(_resolve_trading_calendar(start_time, end_time), fill_value=0.0)


def _benchmark_series_in_qlib(code: str, start_time: str, end_time: str) -> bool:
    try:
        symbols = D.list_instruments({"market": "all", "filter_pipe": []}, start_time=start_time, end_time=end_time, as_list=True)
        return code in symbols
    except Exception:
        return False


def _benchmark_candidates(benchmark: str | None) -> list[str]:
    candidates: list[str] = []
    if benchmark:
        user_norm = _normalize_stock_code_for_qlib(benchmark.strip())
        user_inputs = [benchmark.strip(), user_norm]
        if user_inputs[0] != user_inputs[1]:
            ts_code = _to_tushare_index_code(benchmark.strip())
            user_inputs.append(ts_code or benchmark.strip())
        for item in user_inputs:
            if item and item not in candidates:
                candidates.append(item)
    for fallback in _AUTO_BENCHMARK_CANDIDATES:
        if fallback not in candidates:
            candidates.append(fallback)
    return [x for x in candidates if x]


def _resolve_benchmark(settings: Settings, benchmark: str | None, start_time: str, end_time: str) -> Any:
    candidates = _benchmark_candidates(benchmark)
    tried: list[str] = []
    requested = benchmark.strip() if benchmark else None
    requested_norm = _normalize_stock_code_for_qlib(requested) if requested else None

    for cand in candidates:
        normalized = _normalize_stock_code_for_qlib(cand)
        is_user_requested = requested is not None and normalized == requested_norm
        if normalized not in tried:
            tried.append(normalized)

        if _benchmark_series_in_qlib(normalized, start_time, end_time):
            print(f"[INFO] Using benchmark from qlib data: {normalized}")
            return normalized
        if cand != normalized and _benchmark_series_in_qlib(cand, start_time, end_time):
            print(f"[INFO] Using benchmark from qlib data: {cand}")
            return cand

        index_series = _fetch_index_benchmark_series(cand, start_time, end_time, settings.tushare_token)
        if isinstance(index_series, pd.Series) and not index_series.empty:
            if is_user_requested:
                print(f"[INFO] Using requested benchmark from tushare index API: {cand}")
            elif requested is None:
                print(f"[INFO] Using fallback benchmark from tushare index API: {cand}")
            else:
                print(f"[INFO] Requested benchmark unavailable, fallback to tushare index API: {cand}")
            return index_series

    if requested:
        print(
            f"[WARN] Failed to resolve requested benchmark `{requested}`. "
            f"Tried candidates: {tried}. "
            "Using fallback candidates: "
            + ",".join(_AUTO_BENCHMARK_CANDIDATES)
        )
        print("[WARN] No benchmark series found, fallback to zero return benchmark.")
    else:
        print(
            "[WARN] No benchmark configured and no automatic benchmark found in qlib/tushare data. "
            "Tried candidates: " + ",".join(_AUTO_BENCHMARK_CANDIDATES)
        )
        print("[WARN] Fallback to zero return benchmark.")

    return _build_zero_benchmark(start_time, end_time)


def build_dataset(
    *,
    train: tuple[str, str],
    valid: tuple[str, str],
    test: tuple[str, str],
) -> DatasetH:
    handler = TushareAlpha158Daily(
        instruments="all",
        start_time=train[0],
        end_time=test[1],
        fit_start_time=train[0],
        fit_end_time=train[1],
        shared_processors=[{
            "class": "AshareUniverseFilter",
            "module_path": "tushare_qlib.processors",
            "kwargs": {
                "min_listed_days": 120,
                "min_circ_mv_yuan": 2_000_000_000,
                "min_money_20d_yuan": 20_000_000,
                "exclude_st": True,
                "allow_unknown_st": True,
            },
        }],
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
    _configure_mlflow_tracking(settings)
    qlib.init(provider_uri=str(settings.qlib_data_uri), region=REG_CN, expression_cache=None, dataset_cache=None)
    if train is None or valid is None or test is None:
        train, valid, test = _default_splits_from_data(settings)
        print(f"[INFO] Auto train/valid/test split from data: train={train}, valid={valid}, test={test}")
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI") or _get_mlflow_tracking_uri(settings)
    dataset = build_dataset(train=train, valid=valid, test=test)
    model = LGBModel(
        loss="mse", learning_rate=0.03, num_leaves=63, max_depth=8,
        colsample_bytree=0.8, subsample=0.8, lambda_l1=10.0, lambda_l2=50.0,
        num_threads=12, early_stopping_rounds=100, num_boost_round=2000,
    )

    with R.start(experiment_name=experiment_name, uri=tracking_uri):
        model.fit(dataset)
        R.save_objects(**{"params.pkl": model, "dataset": dataset})
        recorder = R.get_recorder()
        SignalRecord(model=model, dataset=dataset, recorder=recorder).generate()
        SigAnaRecord(recorder=recorder, ana_long_short=True).generate()
        backtest_end = _next_trading_day(test[1], test[0])
        benchmark_cfg = _resolve_benchmark(settings, benchmark, test[0], backtest_end)
        backtest_cfg = {
            "strategy": {
                "class": "TopkDropoutStrategy", "module_path": "qlib.contrib.strategy",
                "kwargs": {"signal": "<PRED>", "topk": topn, "n_drop": 5, "hold_thresh": 5,
                           "only_tradable": True},
            },
            "executor": {
                "class": "SimulatorExecutor", "module_path": "qlib.backtest.executor",
                "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True},
            },
            "backtest": {
                "start_time": test[0], "end_time": backtest_end, "account": 100_000_000,
                "benchmark": benchmark_cfg,
                "exchange_kwargs": {
                    "limit_threshold": 0.095, "deal_price": "close",
                    "open_cost": 0.00035, "close_cost": 0.00085, "min_cost": 5,
                },
            },
        }
        PortAnaRecord(
            recorder=recorder,
            config=backtest_cfg,
        ).generate()

        pred = model.predict(dataset, segment="test")
        if isinstance(pred, pd.DataFrame):
            score = pred.iloc[:, 0]
        else:
            score = pred
        latest = score.index.get_level_values("datetime").max()
        selected = score.xs(latest, level="datetime").sort_values(ascending=False).head(topn)
        output = selected.rename("score").reset_index()
        output.insert(0, "signal_date", latest)
        path = settings.paths.output / f"selection_{pd.Timestamp(latest):%Y%m%d}.csv"
        output.to_csv(path, index=False, encoding="utf-8-sig")
        return path
