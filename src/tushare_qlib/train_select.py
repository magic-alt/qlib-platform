from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .artifacts import ArtifactType, PromotionStatus, stamp_artifact
from .canonical_config import CanonicalConfig
from .lineage import build_lineage, dirty_research_override_enabled, sha256_json
from .settings import Settings
from .store import PartitionStore, sha256_file
from .topk_dropout import TopkDropoutPolicy
from .model_runtime import (
    ModelProfile,
    ResolvedRuntime,
    StageTimings,
    build_model,
    load_model_profile,
    resolved_model_parameters,
    resolve_runtime,
    write_timings,
)
from .research_gate import (
    ResearchPromotionError,
    derive_research_metrics,
    evaluate_research_metrics,
    evaluate_component_metrics,
    write_gate_report,
)

_DEFAULT_BENCHMARK = "SH000300"


def _configure_mlflow_tracking(settings: Settings) -> None:
    if "MLFLOW_TRACKING_URI" not in os.environ:
        db = settings.paths.state / "mlflow.db"
        os.environ["MLFLOW_TRACKING_URI"] = f"sqlite:///{db.resolve()}"
    if "MLFLOW_DEFAULT_ARTIFACT_ROOT" not in os.environ:
        os.environ["MLFLOW_DEFAULT_ARTIFACT_ROOT"] = str((settings.paths.models / "mlruns").resolve())


def _default_splits_from_data(settings: Settings) -> tuple[tuple[str, str], tuple[str, str], tuple[str, str]]:
    dates = pd.to_datetime(
        PartitionStore(settings.paths.raw).list_dates("daily"), format="%Y%m%d", errors="coerce"
    )
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
    dates = pd.to_datetime(
        cal.loc[pd.to_numeric(cal["is_open"], errors="coerce") == 1, "cal_date"], errors="coerce"
    )
    return pd.DatetimeIndex(dates.dropna().sort_values().unique())


def _next_trade_date(settings: Settings, signal_date: str | pd.Timestamp) -> str:
    signal = pd.Timestamp(signal_date).normalize()
    future = _official_calendar(settings)
    future = future[future > signal]
    if len(future) == 0:
        raise ValueError(f"official calendar has no open day after {signal.date()}")
    return str(future[0].strftime("%Y-%m-%d"))


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
    close = (
        pd.Series(pd.to_numeric(frame["close"], errors="coerce").to_numpy(), index=dates)
        .dropna()
        .sort_index()
    )
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
    code = _normalize_stock_code_for_qlib(
        benchmark or str(settings.data.get("research", {}).get("benchmark", _DEFAULT_BENCHMARK))
    )
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

    from .custom_handler import TushareAlpha158Fundamental

    universe = universe or {}
    handler = TushareAlpha158Fundamental(
        instruments=universe.get("instruments", "all"),
        start_time=train[0],
        end_time=test[1],
        fit_start_time=train[0],
        fit_end_time=train[1],
        shared_processors=[
            {
                "class": "AshareUniverseFilter",
                "module_path": "tushare_qlib.processors",
                "kwargs": {
                    "min_listed_days": int(str(universe.get("min_listed_days", 120))),
                    "min_circ_mv_yuan": float(str(universe.get("min_circ_mv_yuan", 2_000_000_000))),
                    "min_money_20d_yuan": float(str(universe.get("min_money_20d_yuan", 20_000_000))),
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


def _prediction_is_unique(settings: Settings, sha256: str, model_id: str) -> bool:
    research_root = settings.paths.output / "research"
    if not research_root.exists():
        return True
    for path in research_root.glob("*/manifest.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(payload.get("externalRunId")) == model_id:
            continue
        lineage = payload.get("lineage", {})
        if isinstance(lineage, dict) and lineage.get("predictionsSha256") == sha256:
            return False
    return True


def _universe_manifest(universe: dict[str, object]) -> tuple[str, list[str] | None]:
    """Return a readable universe label plus static members, when supplied."""

    instruments = universe.get("instruments", "all")
    if isinstance(instruments, (list, tuple, pd.Index)):
        members = [str(instrument) for instrument in instruments]
        return str(universe.get("label", f"static_{len(members)}")), members
    return str(instruments), None


def _selection_volatility(instruments: list[str], latest: pd.Timestamp) -> pd.Series:
    from qlib.data import D

    start = latest - pd.Timedelta(days=60)
    prices = D.features(instruments, ["$close"], start_time=start, end_time=latest, freq="day")
    if prices.empty:
        return pd.Series(index=instruments, data=np.nan)
    close = (
        prices.iloc[:, 0].unstack("instrument")
        if prices.index.names[0] == "datetime"
        else prices.iloc[:, 0].unstack("datetime").T
    )
    return close.pct_change().tail(20).std().reindex(instruments)


def _signal_id(model_id: str, dataset_id: str, signal_date: pd.Timestamp) -> str:
    payload = f"{model_id}|{dataset_id}|{signal_date.isoformat()}".encode()
    return hashlib.sha256(payload).hexdigest()[:24]


def _selection_output(
    selected: pd.Series,
    *,
    signal_date: pd.Timestamp,
    trade_date: str,
    model_id: str,
    dataset_id: str,
    volatility: pd.Series,
) -> pd.DataFrame:
    output = (
        selected.rename("score").reset_index().rename(columns={selected.index.name or "index": "instrument"})
    )
    output.insert(1, "score_rank", np.arange(1, len(output) + 1, dtype=int))
    output["is_model_topk"] = True
    output["volatility"] = output["instrument"].map(volatility)
    output["target_weight"] = 1.0 / len(output) if len(output) else 0.0
    output.insert(0, "trade_date", trade_date)
    output.insert(0, "signal_date", signal_date.strftime("%Y-%m-%d"))
    output["model_id"] = model_id
    output["dataset_id"] = dataset_id
    output["signal_id"] = _signal_id(model_id, dataset_id, signal_date)
    return output


def _selection_volatility_by_date(selections: dict[pd.Timestamp, pd.Series]) -> dict[pd.Timestamp, pd.Series]:
    """Calculate 20-day volatility for every selected instrument in one Qlib query."""
    from qlib.data import D

    dates = pd.DatetimeIndex(selections).sort_values()
    instruments = sorted(
        {str(instrument) for selected in selections.values() for instrument in selected.index}
    )
    prices = D.features(
        instruments,
        ["$close"],
        start_time=dates.min() - pd.Timedelta(days=60),
        end_time=dates.max(),
        freq="day",
    )
    if prices.empty:
        return {date: pd.Series(index=selected.index, data=np.nan) for date, selected in selections.items()}
    close = (
        prices.iloc[:, 0].unstack("instrument")
        if prices.index.names[0] == "datetime"
        else prices.iloc[:, 0].unstack("datetime").T
    )
    rolling_volatility = close.pct_change(fill_method=None).rolling(20).std()
    return {
        date: rolling_volatility.loc[date].reindex(selected.index)
        if date in rolling_volatility.index
        else pd.Series(index=selected.index, data=np.nan)
        for date, selected in selections.items()
    }


def _export_daily_selections(
    settings: Settings,
    score: pd.Series,
    *,
    model_id: str,
    topn: int,
    lineage_id: str,
    manifest_path: Path,
) -> tuple[Path, pd.DataFrame]:
    """Export the top-ranked instruments for every OOS signal date.

    The latest file remains the command result for compatibility with downstream
    execution, while preceding dates make the complete backtest signal history
    directly consumable from ``data/output``.
    """
    if not isinstance(score.index, pd.MultiIndex) or "datetime" not in score.index.names:
        raise ValueError("score must have a MultiIndex containing datetime")

    dates = pd.DatetimeIndex(score.index.get_level_values("datetime").unique()).sort_values()
    if dates.empty:
        raise ValueError("cannot export selections from an empty score series")
    calendar = _official_calendar(settings)
    dataset_id = _dataset_id(settings)
    selections = {
        signal_date: score.xs(signal_date, level="datetime").sort_values(ascending=False).head(topn)
        for signal_date in dates
    }
    volatility_by_date = _selection_volatility_by_date(selections)
    latest_path: Path | None = None
    latest_output: pd.DataFrame | None = None

    for signal_date in dates:
        future = calendar[calendar > signal_date]
        if future.empty:
            raise ValueError(f"official calendar has no open day after {signal_date.date()}")
        selected = selections[signal_date]
        output = _selection_output(
            selected,
            signal_date=signal_date,
            trade_date=future[0].strftime("%Y-%m-%d"),
            model_id=model_id,
            dataset_id=dataset_id,
            volatility=volatility_by_date[signal_date],
        )
        output = stamp_artifact(
            output,
            ArtifactType.MODEL_TOPK,
            promotion_status=PromotionStatus.PROMOTED,
            run_id=model_id,
            model_id=model_id,
            dataset_id=dataset_id,
            lineage_id=lineage_id,
            manifest_path=manifest_path,
        )
        path = settings.paths.output / f"selection_{signal_date:%Y%m%d}.csv"
        output.to_csv(path, index=False, encoding="utf-8-sig")
        latest_path = path
        latest_output = output

    assert latest_path is not None and latest_output is not None
    return latest_path, latest_output


def _export_daily_signal_scores(
    settings: Settings,
    score: pd.Series,
    *,
    model_id: str,
    policy: TopkDropoutPolicy | None = None,
    lineage_id: str,
    manifest_path: Path,
) -> dict[pd.Timestamp, Path]:
    """Persist the full cross-section required to reproduce TopkDropout decisions.

    ``selection_*.csv`` intentionally remains a compact TopN artifact.  The
    matching parquet files are the authoritative score inputs for the exact
    strategy path because current broker holdings may rank outside the TopN.
    """

    if not isinstance(score.index, pd.MultiIndex) or "datetime" not in score.index.names:
        raise ValueError("score must have a MultiIndex containing datetime")
    policy = policy or TopkDropoutPolicy()
    policy.validate()
    dataset_id = _dataset_id(settings)
    output_dir = settings.paths.output / "signals"
    output_dir.mkdir(parents=True, exist_ok=True)
    calendar = _official_calendar(settings)
    paths: dict[pd.Timestamp, Path] = {}
    for signal_date in pd.DatetimeIndex(score.index.get_level_values("datetime").unique()).sort_values():
        future = calendar[calendar > signal_date]
        if future.empty:
            raise ValueError(f"official calendar has no open day after {signal_date.date()}")
        ranked = score.xs(signal_date, level="datetime").sort_values(ascending=False)
        frame = (
            ranked.rename("score").reset_index().rename(columns={ranked.index.name or "index": "instrument"})
        )
        frame.insert(0, "score_rank", np.arange(1, len(frame) + 1, dtype=int))
        frame.insert(0, "trade_date", future[0].strftime("%Y-%m-%d"))
        frame.insert(0, "signal_date", signal_date.strftime("%Y-%m-%d"))
        frame["model_id"] = model_id
        frame["dataset_id"] = dataset_id
        frame["signal_id"] = _signal_id(model_id, dataset_id, signal_date)
        frame["strategy_topk"] = policy.topk
        frame["strategy_n_drop"] = policy.n_drop
        frame["strategy_hold_thresh"] = policy.hold_thresh
        frame["strategy_risk_degree"] = policy.risk_degree
        frame["strategy_only_tradable"] = policy.only_tradable
        frame["strategy_forbid_all_trade_at_limit"] = policy.forbid_all_trade_at_limit
        frame = stamp_artifact(
            frame,
            ArtifactType.MODEL_SCORE,
            promotion_status=PromotionStatus.PROMOTED,
            run_id=model_id,
            model_id=model_id,
            dataset_id=dataset_id,
            lineage_id=lineage_id,
            manifest_path=manifest_path,
        )
        path = output_dir / f"signal_scores_{signal_date:%Y%m%d}.parquet"
        frame.to_parquet(path, index=False)
        paths[signal_date] = path
    return paths


def _backtest_quote_status(settings: Settings, score: pd.Series) -> pd.DataFrame:
    """Load the point-in-time tradability fields used by the daily Qlib exchange."""

    from qlib.data import D

    dates = pd.DatetimeIndex(score.index.get_level_values("datetime").unique()).sort_values()
    calendar = _official_calendar(settings)
    trade_dates = pd.DatetimeIndex(
        [calendar[calendar > date][0] for date in dates if len(calendar[calendar > date])]
    )
    if trade_dates.empty:
        return pd.DataFrame(columns=["trade_date", "instrument", "paused", "is_limit_up", "is_limit_down"])
    instruments = sorted(score.index.get_level_values("instrument").astype(str).unique())
    raw = D.features(
        instruments,
        ["$close", "$is_limit_up", "$is_limit_down"],
        start_time=trade_dates.min(),
        end_time=trade_dates.max(),
        freq="day",
    )
    if raw.empty:
        raise RuntimeError("cannot audit TopkDropout without Qlib trade-status fields")
    frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.normalize()
    frame["paused"] = pd.to_numeric(frame["$close"], errors="coerce").isna().astype(float)
    for column in ("is_limit_up", "is_limit_down"):
        frame[column] = pd.to_numeric(frame[f"${column}"], errors="coerce").fillna(1.0)
    return frame.loc[
        frame["trade_date"].isin(trade_dates),
        ["trade_date", "instrument", "paused", "is_limit_up", "is_limit_down"],
    ]


def train_backtest_select(
    settings: Settings,
    *,
    train: tuple[str, str] | None = None,
    valid: tuple[str, str] | None = None,
    test: tuple[str, str] | None = None,
    benchmark: str | None = None,
    topn: int | None = None,
    experiment_name: str | None = None,
    run_kind: str = "fixed_split",
    model_profile: str | Path | None = None,
    runtime: ResolvedRuntime | None = None,
    promotion_mode: str = "release",
) -> Path:
    if promotion_mode not in {"release", "component"}:
        raise ValueError("promotion_mode must be 'release' or 'component'")
    if runtime is not None and model_profile is not None:
        raise ValueError("pass either model_profile or a pre-resolved runtime, not both")
    profile: ModelProfile = (
        runtime.profile if runtime is not None else load_model_profile(settings, model_profile)
    )
    runtime = runtime or resolve_runtime(profile)
    timings = StageTimings()
    try:
        import qlib
        from qlib.constant import REG_CN
        from qlib.workflow import R
        from qlib.workflow.record_temp import PortAnaRecord, SigAnaRecord, SignalRecord
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("Install the qlib optional dependencies: pip install -e '.[qlib]'") from exc

    research = (
        settings.data.get("research", {}) if isinstance(settings.data.get("research", {}), dict) else {}
    )
    universe = dict(settings.data.get("universe", {}))
    seed = int(research.get("random_seed", 42))
    np.random.seed(seed)
    _configure_mlflow_tracking(settings)
    with timings.measure("data_seconds"):
        # Alpha158 materializes a wide cross-sectional frame. Keeping feature
        # preparation single-process avoids duplicate worker copies.
        qlib.init(
            provider_uri=str(settings.qlib_data_uri),
            region=REG_CN,
            expression_cache=None,
            dataset_cache=None,
            kernels=1,
        )
        if train is None or valid is None or test is None:
            train, valid, test = _default_splits_from_data(settings)
        if not (
            pd.Timestamp(train[1]) < pd.Timestamp(valid[0]) <= pd.Timestamp(valid[1]) < pd.Timestamp(test[0])
        ):
            raise ValueError("train/valid/test windows must be strictly chronological and non-overlapping")
        dataset = build_dataset(train=train, valid=valid, test=test, universe=universe)
    feature_columns = [str(column) for column in dataset.handler.get_cols(col_set="feature")]
    feature_count = len(feature_columns)
    model_parameters = resolved_model_parameters(
        runtime,
        feature_count=feature_count,
        seed=seed,
        num_threads=int(research.get("num_threads", 8)),
    )
    canonical = CanonicalConfig.from_settings(
        settings,
        runtime,
        topk_override=topn,
        model_parameters=model_parameters,
    )
    topk_policy = canonical.strategy.to_policy()
    model = build_model(
        runtime,
        feature_count=feature_count,
        seed=seed,
        num_threads=int(research.get("num_threads", 8)),
    )

    tracking_uri = os.environ["MLFLOW_TRACKING_URI"]
    resolved_experiment = experiment_name or f"tushare_alpha158_{runtime.profile.family}"
    with R.start(experiment_name=resolved_experiment, uri=tracking_uri):
        recorder = R.get_recorder()
        recorder.log_params(
            model_profile=runtime.profile.name,
            model_family=runtime.profile.family,
            requested_device=runtime.profile.device,
            resolved_device=runtime.resolved_device,
            runtime_fingerprint=runtime.fingerprint,
            device_fallback_reason=runtime.fallback_reason or "",
            feature_count=feature_count,
        )
        with timings.measure("train_seconds"):
            model.fit(dataset)
        R.save_objects(**{"params.pkl": model, "dataset": dataset})
        signal_record = SignalRecord(model=model, dataset=dataset, recorder=recorder)
        with timings.measure("predict_seconds"):
            pred = model.predict(dataset)
            if isinstance(pred, pd.Series):
                pred = pred.to_frame("score")
        signal_record.save(**{"pred.pkl": pred})
        with timings.measure("signal_analysis_seconds"):
            raw_label = signal_record.generate_label(dataset)
            signal_record.save(**{"label.pkl": raw_label})
            SigAnaRecord(recorder=recorder, ana_long_short=True, ann_scaler=252).generate()
        pred_dates = pred.index.get_level_values("datetime")
        oos_start = pd.Timestamp(pred_dates.min()).strftime("%Y-%m-%d")
        oos_end = pd.Timestamp(pred_dates.max()).strftime("%Y-%m-%d")
        benchmark_cfg = _resolve_benchmark(settings, benchmark, oos_start, oos_end)
        with timings.measure("backtest_seconds"):
            PortAnaRecord(
                recorder=recorder,
                config={
                    "strategy": {
                        "class": "TopkDropoutStrategy",
                        "module_path": "qlib.contrib.strategy",
                        "kwargs": {
                            "signal": "<PRED>",
                            "topk": topk_policy.topk,
                            "n_drop": topk_policy.n_drop,
                            "hold_thresh": topk_policy.hold_thresh,
                            "only_tradable": topk_policy.only_tradable,
                            "forbid_all_trade_at_limit": topk_policy.forbid_all_trade_at_limit,
                            "risk_degree": topk_policy.risk_degree,
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
                        "account": int(research.get("backtest_account", 500_000)),
                        "benchmark": benchmark_cfg,
                        "exchange_kwargs": {
                            "limit_threshold": ("$is_limit_up > 0", "$is_limit_down > 0"),
                            "deal_price": str(research.get("deal_price", "open")),
                            "volume_threshold": (
                                "current",
                                f"$volume * {float(research.get('max_participation_rate', 0.05))}",
                            ),
                            "trade_unit": int(research.get("trade_unit", 100)),
                            "open_cost": float(research.get("open_cost", 0.00035)),
                            "close_cost": float(research.get("close_cost", 0.00085)),
                            "min_cost": float(research.get("min_cost", 5)),
                        },
                    },
                },
            ).generate()

        model_id = str(getattr(recorder, "id", "unversioned"))
        artifact_dir = settings.paths.output / "research" / model_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        pred_path = artifact_dir / "oos_predictions.parquet"
        report_path = artifact_dir / "portfolio_report.parquet"
        audit_path = artifact_dir / "strategy_audit.parquet"
        holdings_path = artifact_dir / "holdings.parquet"
        label_path = artifact_dir / "oos_labels.parquet"
        timings_path = artifact_dir / "timings.json"
        gate_path = artifact_dir / "research_gate.json"
        manifest_path = artifact_dir / "manifest.json"
        from .backtest_report import ReportArtifacts, export_holding_snapshots, write_backtest_report
        from .strategy_audit import build_strategy_audit

        with timings.measure("artifact_export_seconds"):
            score = pred.iloc[:, 0]
            latest = pd.Timestamp(score.index.get_level_values("datetime").max())
            report = recorder.load_object("portfolio_analysis/report_normal_1day.pkl")
            pred.to_parquet(pred_path)
            label_frame = raw_label.to_frame("label") if isinstance(raw_label, pd.Series) else raw_label
            label_frame.to_parquet(label_path)
            report.to_parquet(report_path)
            positions = recorder.load_object("portfolio_analysis/positions_normal_1day.pkl")
            holdings = export_holding_snapshots(positions)
            holdings.to_parquet(holdings_path, index=False)
            audit = build_strategy_audit(
                score,
                positions,
                recorder.load_object("portfolio_analysis/indicators_normal_1day_obj.pkl"),
                _backtest_quote_status(settings, score),
                policy=topk_policy,
            )
            audit.to_parquet(audit_path, index=False)
            metrics: dict[str, float] = {}
            for column in ("return", "bench", "cost"):
                if column in report:
                    values = pd.to_numeric(report[column], errors="coerce").dropna()
                    metrics[f"{column}Total"] = float((1.0 + values).prod() - 1.0)
            lineage = build_lineage(
                settings,
                canonical,
                dataset_fingerprint=_dataset_id(settings),
                feature_columns=feature_columns,
            )
            predictions_sha256 = sha256_file(pred_path)
            lineage["predictionsSha256"] = predictions_sha256
            lineage["lineageId"] = sha256_json(
                {key: value for key, value in lineage.items() if key != "lineageId"}
            )[:32]
            gate_metrics = derive_research_metrics(
                pred,
                raw_label,
                report,
                unique_artifact=_prediction_is_unique(settings, predictions_sha256, model_id),
                lineage_complete=bool(lineage["complete"]),
            )
            dirty_research_override = dirty_research_override_enabled(settings, lineage)
            gate_metrics["dirty_research_override"] = dirty_research_override
            gate_report = (
                evaluate_component_metrics(gate_metrics, allow_dirty_research=dirty_research_override)
                if promotion_mode == "component"
                else evaluate_research_metrics(
                    gate_metrics,
                    canonical.promotion,
                    allow_dirty_research=dirty_research_override,
                )
            )
            if dirty_research_override and gate_report["passed"]:
                gate_report["decision"] = "RESEARCH_ONLY"
            write_gate_report(gate_report, gate_path)
            gate_passed = bool(gate_report["passed"])
            promoted = gate_passed and promotion_mode == "release" and bool(lineage["complete"])

            path: Path | None = None
            output: pd.DataFrame | None = None
            signal_paths: dict[pd.Timestamp, Path] = {}
            if promoted:
                signal_paths = _export_daily_signal_scores(
                    settings,
                    score,
                    model_id=model_id,
                    policy=topk_policy,
                    lineage_id=str(lineage["lineageId"]),
                    manifest_path=manifest_path,
                )
                path, output = _export_daily_selections(
                    settings,
                    score,
                    model_id=model_id,
                    topn=topk_policy.topk,
                    lineage_id=str(lineage["lineageId"]),
                    manifest_path=manifest_path,
                )
        timing_payload = timings.to_dict()
        recorder.log_metrics(
            **{f"timing.{key}": float(value) for key, value in timing_payload["phasesSeconds"].items()},
            **{"timing.total_seconds": float(timing_payload["totalSeconds"])},
        )
        write_timings(timings_path, runtime, timing_payload)
        lineage_universe = lineage.get("universe", {})
        source_snapshot_id = (
            lineage_universe.get("sourceSnapshotId") if isinstance(lineage_universe, dict) else None
        )
        dataset_manifest: dict[str, object] = {
            "fingerprint": _dataset_id(settings),
            "datasetId": canonical.dataset.dataset_id,
            "source": canonical.dataset.source,
            "universe": {
                "name": canonical.dataset.universe_name,
                "membershipType": canonical.dataset.membership_type,
                "source": canonical.dataset.source,
                "membershipSnapshotHash": lineage["universeSpecSha256"],
                "sourceSnapshotId": source_snapshot_id,
                "secondaryFilters": canonical.dataset.secondary_filters,
            },
            "startDate": train[0],
            "endDate": oos_end,
        }
        manifest = {
            "schemaVersion": "2.0",
            "externalRunId": model_id,
            "runKind": run_kind,
            "name": f"Qlib {run_kind} {oos_start}..{oos_end}",
            "dataset": dataset_manifest,
            "model": {
                "name": "Alpha158-LGBM" if runtime.profile.family == "lightgbm" else "Alpha158-DNN",
                "fingerprint": model_id,
                "parameters": model_parameters,
            },
            "canonicalConfig": canonical.to_manifest(),
            "portfolioPolicySha256": sha256_json(canonical.to_manifest()["portfolio"]),
            "lineage": lineage,
            "promotion": {
                "status": (
                    PromotionStatus.PROMOTED.value
                    if promoted
                    else PromotionStatus.CANDIDATE.value
                    if gate_passed
                    else PromotionStatus.REJECTED.value
                ),
                "decision": gate_report["decision"],
                "gateReportPath": str(gate_path),
                "gateMode": "component_validation" if promotion_mode == "component" else "release",
            },
            "runtime": runtime.to_manifest(),
            "timings": timing_payload,
            "folds": [
                {"key": run_kind, "train": list(train), "valid": list(valid), "test": [oos_start, oos_end]}
            ],
            "execution": {
                "benchmark": benchmark or str(research.get("benchmark", _DEFAULT_BENCHMARK)),
                "signalLagDays": int(research.get("signal_lag_days", 1)),
                "dealPrice": str(research.get("deal_price", "open")),
                "tradeUnit": int(research.get("trade_unit", 100)),
                "maxParticipationRate": float(research.get("max_participation_rate", 0.05)),
                "topkDropout": topk_policy.__dict__,
            },
            "metrics": {**metrics, **gate_metrics},
            "artifacts": [
                {"name": pred_path.name, "localPath": str(pred_path), "rows": len(pred)},
                {"name": label_path.name, "localPath": str(label_path), "rows": len(label_frame)},
                {"name": report_path.name, "localPath": str(report_path), "rows": len(report)},
                {"name": audit_path.name, "localPath": str(audit_path), "rows": len(audit)},
                {"name": holdings_path.name, "localPath": str(holdings_path), "rows": len(holdings)},
                {"name": timings_path.name, "localPath": str(timings_path)},
                {"name": gate_path.name, "localPath": str(gate_path)},
                *ReportArtifacts(
                    markdown_path=artifact_dir / "backtest_report.md",
                    pdf_path=artifact_dir / "backtest_report.pdf",
                    assets_dir=artifact_dir / "report_assets",
                ).manifest_entries(),
            ],
        }
        if promoted:
            assert output is not None and path is not None
            manifest["latestTargets"] = {
                "artifactType": ArtifactType.MODEL_TOPK.value,
                "schemaVersion": "2.0",
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
                "scorePath": str(signal_paths[latest]),
            }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        write_backtest_report(settings, artifact_dir)
        if not gate_passed:
            raise ResearchPromotionError(manifest_path)
        if promotion_mode == "component" or not promoted:
            return manifest_path
        assert path is not None
        return path
