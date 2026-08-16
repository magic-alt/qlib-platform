from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .alpha.base import AlphaPackSpec
from .alpha.registry import (
    alpha_pack_from_settings,
    assert_alpha_pack_compatible,
    handler_class,
)
from .artifacts import ArtifactType, PromotionStatus, stamp_artifact
from .canonical_config import CanonicalConfig
from .lineage import build_lineage, dirty_research_override_enabled, sha256_json
from .settings import Settings
from .store import sha256_file
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
from .prediction_snapshot import (
    PredictionSnapshotSpec,
    prediction_snapshot_path,
    write_prediction_snapshot,
)
from .processor_state import processor_state_manifest
from .research_gate import (
    ResearchPromotionError,
    ResearchThresholds,
    derive_research_metrics,
    derive_signal_metrics,
    evaluate_component_metrics,
    evaluate_research_metrics,
    evaluate_signal_metrics,
    write_gate_report,
)
from .research_timing import (
    LabelSpec,
    label_spec_from_settings,
    label_timing_from_settings,
    shared_research_calendar,
)
from .research_experiment import ResearchExperimentSpec
from .feature_store import feature_store_enabled, prepare_feature_data
from .dataset_resolver import pin_dataset
from .runtime_safety import resolve_qlib_parallel_runtime

_DEFAULT_BENCHMARK = "SH000300"


def _sqlite_tracking_uri(path: Path) -> str:
    return f"sqlite:///{path.resolve().as_posix()}"


def _configure_mlflow_tracking(settings: Settings) -> None:
    if "MLFLOW_TRACKING_URI" not in os.environ:
        db = settings.paths.state / "mlflow.db"
        os.environ["MLFLOW_TRACKING_URI"] = _sqlite_tracking_uri(db)
    if "MLFLOW_DEFAULT_ARTIFACT_ROOT" not in os.environ:
        os.environ["MLFLOW_DEFAULT_ARTIFACT_ROOT"] = str((settings.paths.models / "mlruns").resolve())


def _research_label_horizon_days(settings: Settings) -> int:
    return label_timing_from_settings(settings).horizon_days


def _promotion_authorized(promotion_mode: str, promoted: bool) -> bool:
    return promotion_mode == "release" and promoted


def _align_oos_labels(
    predictions: pd.Series | pd.DataFrame,
    raw_labels: pd.Series | pd.DataFrame,
) -> pd.DataFrame:
    """Return the label payload on exactly the model's OOS inference index."""

    prediction_index = predictions.index
    label_frame = raw_labels.to_frame("label") if isinstance(raw_labels, pd.Series) else raw_labels.copy()
    if "label" not in label_frame:
        if len(label_frame.columns) != 1:
            raise ValueError("OOS labels must contain exactly one label column")
        label_frame = label_frame.rename(columns={label_frame.columns[0]: "label"})
    expected_names = ["datetime", "instrument"]
    if not isinstance(prediction_index, pd.MultiIndex) or prediction_index.names != expected_names:
        raise ValueError("OOS predictions require a datetime/instrument MultiIndex")
    if not isinstance(label_frame.index, pd.MultiIndex) or label_frame.index.names != expected_names:
        raise ValueError("OOS labels require a datetime/instrument MultiIndex")
    if prediction_index.has_duplicates:
        raise ValueError("OOS predictions contain duplicate datetime/instrument rows")
    if label_frame.index.has_duplicates:
        raise ValueError("OOS labels contain duplicate datetime/instrument rows")
    missing_labels = prediction_index.difference(label_frame.index)
    if len(missing_labels):
        raise ValueError(f"OOS predictions have {len(missing_labels)} rows without labels")
    aligned = label_frame[["label"]].reindex(prediction_index)
    if not aligned.index.equals(prediction_index):
        raise ValueError("OOS label alignment did not preserve the prediction index")
    return aligned


def _default_splits_from_data(settings: Settings) -> tuple[tuple[str, str], tuple[str, str], tuple[str, str]]:
    dates = shared_research_calendar(settings)
    research = settings.data.get("research", {})
    min_history = int(research.get("min_history_days", 756)) if isinstance(research, dict) else 756
    if len(dates) < min_history:
        raise ValueError(
            f"Commercial research gate requires at least {min_history} shared raw/Qlib trading days; "
            f"detected {len(dates)}. "
            "Pass explicit windows only for a labelled smoke test, never for model promotion."
        )
    thresholds = ResearchThresholds.from_mapping(
        research.get("promotion_thresholds", {}) if isinstance(research, dict) else {}
    )
    label_buffer_days = label_timing_from_settings(settings).lookahead_days
    n = len(dates)
    test_days = thresholds.min_observations + label_buffer_days
    # Test ends one day before the last calendar entry so the backtest
    # strategy can peek at the next trading day for its final step.
    test_end_idx = n - 2
    test_start_idx = test_end_idx - test_days + 1
    if test_start_idx < 2:
        raise ValueError(
            f"Not enough pre-holdout history after reserving {test_days} test days and one future trading day"
        )
    # Preserve the former 3:1 train/validation ratio while making the release
    # holdout large enough to satisfy the configured observation threshold.
    train_end_idx = (test_start_idx * 3) // 4 - 1
    valid_end_idx = test_start_idx - 1
    return (
        (dates[0].strftime("%Y-%m-%d"), dates[train_end_idx].strftime("%Y-%m-%d")),
        (dates[train_end_idx + 1].strftime("%Y-%m-%d"), dates[valid_end_idx].strftime("%Y-%m-%d")),
        (dates[test_start_idx].strftime("%Y-%m-%d"), dates[test_end_idx].strftime("%Y-%m-%d")),
    )


def _official_calendar(settings: Settings) -> pd.DatetimeIndex:
    if settings.uses_platform_release():
        from .platform_release import load_platform_release

        release = load_platform_release(settings)
        cal = pd.concat(
            (pd.read_parquet(path) for path in release.files("trading_calendar")),
            ignore_index=True,
        )
        owner = f"DataRelease {release.data_release_id} trading_calendar component"
    else:
        path = settings.paths.metadata / "trade_calendar.parquet"
        if not path.exists():
            raise FileNotFoundError(f"official trading calendar is required: {path}")
        cal = pd.read_parquet(path)
        owner = str(path)
    required = {"cal_date", "is_open"}
    if not required.issubset(cal.columns):
        raise ValueError(
            f"official calendar missing columns in {owner}: {sorted(required - set(cal.columns))}"
        )
    dates = pd.to_datetime(
        cal.loc[pd.to_numeric(cal["is_open"], errors="coerce") == 1, "cal_date"], errors="coerce"
    )
    result = pd.DatetimeIndex(dates.dropna().sort_values().unique()).normalize()
    if result.empty:
        raise ValueError(f"official calendar contains no open dates in {owner}")
    return result


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
    if settings.uses_platform_release():
        from .platform_release import load_platform_release

        release = load_platform_release(settings)
        paths = release.files("benchmark")
        frame = pd.concat((pd.read_parquet(path) for path in paths), ignore_index=True)
        tushare_code = _to_tushare_index_code(code)
        if "ts_code" in frame.columns and tushare_code:
            frame = frame.loc[frame["ts_code"].astype(str).str.upper() == tushare_code]
        owner = f"DataRelease {release.data_release_id} benchmark component"
    else:
        path = settings.paths.metadata / "benchmarks" / f"{code.upper()}.parquet"
        if not path.exists():
            raise FileNotFoundError(
                f"Local benchmark is required: {path}. "
                f"Run `tq --config {settings.config_path} sync-benchmark`."
            )
        frame = pd.read_parquet(path)
        owner = str(path)
    required = {"trade_date", "close"}
    if not required.issubset(frame.columns):
        raise ValueError(f"benchmark file missing columns: {sorted(required - set(frame.columns))}")
    if frame.empty:
        raise ValueError(f"benchmark {code} contains no rows in {owner}")
    dates = pd.to_datetime(frame["trade_date"], errors="coerce")
    close = (
        pd.Series(pd.to_numeric(frame["close"], errors="coerce").to_numpy(), index=dates)
        .dropna()
        .sort_index()
    )
    if close.index.duplicated().any():
        raise ValueError(f"duplicate benchmark dates in {owner}")
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
    label_spec: LabelSpec,
    alpha_pack: AlphaPackSpec,
    prepared_feature_data: pd.DataFrame | None = None,
    feature_set_id: str | None = None,
    selected_technical: tuple[str, ...] = (),
) -> Any:
    from qlib.contrib.data.handler import check_transform_proc
    from qlib.data.dataset import DatasetH
    from qlib.data.dataset.handler import DataHandlerLP
    from qlib.data.dataset.loader import QlibDataLoader, StaticDataLoader

    universe = universe or {}
    handler_type = handler_class(alpha_pack)
    label = label_spec.qlib_config()
    shared_processors = [
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
    ]
    infer_processors = []
    if alpha_pack.processor_recipe == "phase2_feature_set_v1":
        if not feature_set_id:
            raise ValueError("Phase 2 alpha pack requires experiment.alpha.feature_set")
        infer_processors.append(
            {
                "class": "Phase2FeatureSetProcessor",
                "module_path": "tushare_qlib.processors",
                "kwargs": {
                    "feature_set_id": feature_set_id,
                    "selected_technical": list(selected_technical),
                },
            }
        )
        infer_processors.append(
            {"class": "ProcessInfSingleThread", "module_path": "tushare_qlib.processors", "kwargs": {}}
        )
    elif alpha_pack.processor_recipe == "multifactor_cross_section_v1":
        infer_processors.append(
            {
                "class": "CrossSectionalFactorProcessor",
                "module_path": "tushare_qlib.processors",
                "kwargs": {"minimum_industry_members": 5},
            }
        )
        infer_processors.append(
            {"class": "ProcessInfSingleThread", "module_path": "tushare_qlib.processors", "kwargs": {}}
        )
    else:
        infer_processors.append(
            {"class": "ProcessInfSingleThread", "module_path": "tushare_qlib.processors", "kwargs": {}}
        )
        infer_processors.append(
            {"class": "RobustZScoreNorm", "kwargs": {"fields_group": "feature", "clip_outlier": True}}
        )
    infer_processors.append({"class": "Fillna", "kwargs": {"fields_group": "feature"}})
    learn_processors = [
        {"class": "DropnaLabel"},
        {"class": "CSRankNorm", "kwargs": {"fields_group": "label"}},
    ]
    if prepared_feature_data is None:
        handler = handler_type(
            instruments=universe.get("instruments", "all"),
            start_time=train[0],
            end_time=test[1],
            fit_start_time=train[0],
            fit_end_time=train[1],
            label=label,
            shared_processors=shared_processors,
            infer_processors=infer_processors,
            learn_processors=learn_processors,
        )
    else:
        # StaticDataLoader contains raw expressions only. Every fold receives
        # fresh processors whose learnable state is fitted on that fold's train span.
        infer_processors = check_transform_proc(infer_processors, train[0], train[1])
        label_frame = QlibDataLoader({"label": label}).load(
            instruments=universe.get("instruments", "all"),
            start_time=train[0],
            end_time=test[1],
        )
        prepared_feature_data = prepared_feature_data.join(label_frame, how="left")
        learn_processors = check_transform_proc(learn_processors, train[0], train[1])
        handler = DataHandlerLP(
            instruments=None,
            start_time=train[0],
            end_time=test[1],
            data_loader=StaticDataLoader(prepared_feature_data),
            shared_processors=shared_processors,
            infer_processors=infer_processors,
            learn_processors=learn_processors,
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
    target-portfolio export, while preceding dates make the complete backtest signal history
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
    strategy path because current research holdings may rank outside the TopN.
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


def _backtest_quote_status(
    settings: Settings,
    score: pd.Series,
    timings: StageTimings | None = None,
) -> pd.DataFrame:
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
    from contextlib import nullcontext

    query_timer = (
        timings.measure_diagnostic("audit_quote_query_seconds") if timings is not None else nullcontext()
    )
    with query_timer:
        raw = D.features(
            instruments,
            ["$close", "$is_limit_up", "$is_limit_down"],
            start_time=trade_dates.min(),
            end_time=trade_dates.max(),
            freq="day",
        )
    if raw.empty:
        raise RuntimeError("cannot audit TopkDropout without Qlib trade-status fields")
    transform_timer = (
        timings.measure_diagnostic("audit_quote_transform_seconds") if timings is not None else nullcontext()
    )
    with transform_timer:
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
    prepared_feature_data: pd.DataFrame | None = None,
    feature_store_metadata: dict[str, object] | None = None,
    artifact_level: str = "full",
) -> Path:
    settings, pinned_dataset = pin_dataset(settings)
    if promotion_mode not in {"release", "component", "signal", "holdout"}:
        raise ValueError("promotion_mode must be 'release', 'component', 'signal', or 'holdout'")
    if runtime is not None and model_profile is not None:
        raise ValueError("pass either model_profile or a pre-resolved runtime, not both")
    if artifact_level not in {"minimal", "full"}:
        raise ValueError("artifact_level must be 'minimal' or 'full'")
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
        raise RuntimeError(
            "Install the fixed Qlib checkout into the active environment before running this command."
        ) from exc

    research = (
        settings.data.get("research", {}) if isinstance(settings.data.get("research", {}), dict) else {}
    )
    label_spec = label_spec_from_settings(settings)
    label_horizon_days = label_spec.horizon_days
    universe = dict(settings.data.get("universe", {}))
    seed = int(research.get("random_seed", 42))
    alpha_pack = alpha_pack_from_settings(settings)
    assert_alpha_pack_compatible(settings, alpha_pack)
    experiment_config = settings.data.get("experiment", {})
    experiment_config = experiment_config if isinstance(experiment_config, dict) else {}
    alpha_config = experiment_config.get("alpha", {})
    alpha_config = alpha_config if isinstance(alpha_config, dict) else {}
    feature_set_id = str(alpha_config.get("feature_set") or "").strip() or None
    selected_technical = tuple(str(value) for value in alpha_config.get("selected_technical", ()))
    np.random.seed(seed)
    _configure_mlflow_tracking(settings)
    parallel = resolve_qlib_parallel_runtime(settings)
    with timings.measure("qlib_init_seconds"):
        qlib.init(
            provider_uri=str(settings.qlib_data_uri),
            region=REG_CN,
            expression_cache=None,
            dataset_cache=None,
            **parallel.qlib_init_kwargs(),
        )
    with timings.measure("dataset_prepare_seconds"):
        if train is None or valid is None or test is None:
            train, valid, test = _default_splits_from_data(settings)
        if not (
            pd.Timestamp(train[1]) < pd.Timestamp(valid[0]) <= pd.Timestamp(valid[1]) < pd.Timestamp(test[0])
        ):
            raise ValueError("train/valid/test windows must be strictly chronological and non-overlapping")
    if prepared_feature_data is None and feature_store_enabled(settings):
        with timings.measure("feature_store_seconds"):
            prepared_feature_data, feature_store_metadata = prepare_feature_data(settings, train[0], test[1])
    with timings.measure("handler_process_seconds"):
        dataset = build_dataset(
            train=train,
            valid=valid,
            test=test,
            universe=universe,
            label_spec=label_spec,
            alpha_pack=alpha_pack,
            prepared_feature_data=prepared_feature_data,
            feature_set_id=feature_set_id,
            selected_technical=selected_technical,
        )
    fitted_processor_state = processor_state_manifest(dataset.handler, train)
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
    experiment_spec = ResearchExperimentSpec.resolve(
        settings,
        runtime=runtime,
        canonical=canonical,
        alpha_pack=alpha_pack,
        label_spec=label_spec,
        train=train,
        valid=valid,
        test=test,
        run_kind=run_kind,
        benchmark=str(benchmark or research.get("benchmark") or _DEFAULT_BENCHMARK),
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
        with timings.measure("model_save_seconds"):
            R.save_objects(**{"params.pkl": model})
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
        best_iteration = getattr(getattr(model, "model", None), "best_iteration", None)
        if best_iteration is not None:
            recorder.log_params(best_iteration=int(best_iteration))
        if promotion_mode in {"signal", "component"}:
            component_mode = promotion_mode == "component"
            model_id = str(getattr(recorder, "id", "unversioned"))
            artifact_dir = settings.paths.output / "research" / model_id
            artifact_dir.mkdir(parents=True, exist_ok=True)
            pred_path = artifact_dir / "oos_predictions.parquet"
            pred_snapshot_path = prediction_snapshot_path(pred_path)
            label_path = artifact_dir / "oos_labels.parquet"
            timings_path = artifact_dir / "timings.json"
            gate_path = artifact_dir / ("component_gate.json" if component_mode else "signal_gate.json")
            manifest_path = artifact_dir / "manifest.json"
            with timings.measure("artifact_export_seconds"):
                label_frame = _align_oos_labels(pred, raw_label)
                prediction_snapshot = write_prediction_snapshot(
                    pred_path,
                    pred,
                    labels=label_frame,
                    spec=PredictionSnapshotSpec.from_experiment(
                        experiment_spec,
                        feature_snapshot_id=str(
                            (feature_store_metadata or {}).get("featureSnapshotId")
                            or f"inline_{alpha_pack.fingerprint}"
                        ),
                        model_id=model_id,
                        fold_id=run_kind,
                    ),
                )
                label_frame.to_parquet(label_path)
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
                dirty_override = dirty_research_override_enabled(settings, lineage)
                signal_metrics = derive_signal_metrics(
                    pred,
                    raw_label,
                    unique_artifact=_prediction_is_unique(settings, predictions_sha256, model_id),
                    lineage_complete=bool(lineage["complete"]),
                    label_horizon_days=label_horizon_days,
                )
                signal_metrics["dirty_research_override"] = dirty_override
                gate_report = (
                    evaluate_component_metrics(signal_metrics, allow_dirty_research=dirty_override)
                    if component_mode
                    else evaluate_signal_metrics(
                        signal_metrics,
                        canonical.promotion,
                        allow_dirty_research=dirty_override,
                    )
                )
                write_gate_report(gate_report, gate_path)
            timing_payload = timings.to_dict()
            write_timings(timings_path, runtime, timing_payload)
            recorder.log_metrics(
                **{f"timing.{key}": float(value) for key, value in timing_payload["phasesSeconds"].items()}
            )
            manifest = {
                "schemaVersion": "2.0",
                "externalRunId": model_id,
                "runKind": run_kind if component_mode else "signal_screen",
                "name": (
                    f"Qlib rolling OOS signal component {oos_start}..{oos_end}"
                    if component_mode
                    else f"Qlib signal screen {oos_start}..{oos_end}"
                ),
                "dataset": {
                    "fingerprint": _dataset_id(settings),
                    "versionId": pinned_dataset.version_id,
                    "datasetId": canonical.dataset.dataset_id,
                    "source": canonical.dataset.source,
                    "universe": canonical.dataset.universe_name,
                    "startDate": train[0],
                    "endDate": oos_end,
                },
                "featureStore": feature_store_metadata,
                "processorState": fitted_processor_state,
                "model": {
                    "name": runtime.profile.name,
                    "fingerprint": model_id,
                    "parameters": model_parameters,
                    "bestIteration": int(best_iteration) if best_iteration is not None else None,
                    "labelHorizonDays": label_horizon_days,
                },
                "canonicalConfig": canonical.to_manifest(),
                "researchExperimentId": experiment_spec.experiment_id,
                "researchExperiment": experiment_spec.to_manifest(),
                "phase2Hypothesis": experiment_spec.hypothesis_manifest(),
                "predictionSnapshot": prediction_snapshot,
                "lineage": lineage,
                "promotion": {
                    "status": (
                        "CANDIDATE"
                        if component_mode and gate_report["passed"]
                        else "SCREENED"
                        if gate_report["passed"]
                        else "REJECTED"
                    ),
                    "decision": gate_report["decision"],
                    "gateMode": "component_validation" if component_mode else "signal_screen",
                    "promotionAuthorized": False,
                },
                "runtime": runtime.to_manifest(),
                "timings": timing_payload,
                "folds": [
                    {
                        "key": run_kind,
                        "train": list(train),
                        "valid": list(valid),
                        "test": [oos_start, oos_end],
                    }
                ],
                "metrics": signal_metrics,
                "artifacts": [
                    {"name": pred_path.name, "localPath": str(pred_path), "rows": len(pred)},
                    {"name": pred_snapshot_path.name, "localPath": str(pred_snapshot_path)},
                    {"name": label_path.name, "localPath": str(label_path), "rows": len(label_frame)},
                    {"name": timings_path.name, "localPath": str(timings_path)},
                    {"name": gate_path.name, "localPath": str(gate_path)},
                ],
            }
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            from .dataset_registry import DatasetRegistry

            DatasetRegistry(settings.registry_path).register_research_manifest(manifest_path)
            return manifest_path
        with timings.measure("benchmark_load_seconds"):
            benchmark_cfg = _resolve_benchmark(settings, benchmark, oos_start, oos_end)
        with timings.measure("portfolio_engine_seconds"):
            from .topk_dropout import enforce_deterministic_qlib_position_order

            enforce_deterministic_qlib_position_order()
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
        pred_snapshot_path = prediction_snapshot_path(pred_path)
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
            with timings.measure_diagnostic("portfolio_artifact_load_seconds"):
                report = recorder.load_object("portfolio_analysis/report_normal_1day.pkl")
                positions = recorder.load_object("portfolio_analysis/positions_normal_1day.pkl")
                indicators = recorder.load_object("portfolio_analysis/indicators_normal_1day_obj.pkl")
            label_frame = _align_oos_labels(pred, raw_label)
            prediction_snapshot = write_prediction_snapshot(
                pred_path,
                pred,
                labels=label_frame,
                spec=PredictionSnapshotSpec.from_experiment(
                    experiment_spec,
                    feature_snapshot_id=str(
                        (feature_store_metadata or {}).get("featureSnapshotId")
                        or f"inline_{alpha_pack.fingerprint}"
                    ),
                    model_id=model_id,
                    fold_id=run_kind,
                ),
            )
            label_frame.to_parquet(label_path)
            report.to_parquet(report_path)
            with timings.measure_diagnostic("holdings_build_seconds"):
                holdings = export_holding_snapshots(positions)
            holdings.to_parquet(holdings_path, index=False)
            quote_status = _backtest_quote_status(settings, score, timings)
            with timings.measure_diagnostic("audit_build_seconds"):
                audit = build_strategy_audit(
                    score,
                    positions,
                    indicators,
                    quote_status,
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
                label_horizon_days=label_horizon_days,
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
            review_required = bool(gate_report.get("reviewRequired", False))
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
            "handlerRows": len(dataset.handler._learn),
            "instrumentCount": int(dataset.handler._learn.index.get_level_values("instrument").nunique()),
            "featureCount": feature_count,
        }
        report_artifacts = (
            ReportArtifacts(
                markdown_path=artifact_dir / "backtest_report.md",
                pdf_path=artifact_dir / "backtest_report.pdf",
                assets_dir=artifact_dir / "report_assets",
            ).manifest_entries()
            if artifact_level == "full"
            else []
        )
        manifest = {
            "schemaVersion": "2.0",
            "externalRunId": model_id,
            "runKind": run_kind,
            "name": f"Qlib {run_kind} {oos_start}..{oos_end}",
            "dataset": dataset_manifest,
            "featureStore": feature_store_metadata,
            "processorState": fitted_processor_state,
            "model": {
                "name": runtime.profile.name,
                "fingerprint": model_id,
                "parameters": model_parameters,
                "labelHorizonDays": label_horizon_days,
                "bestIteration": int(best_iteration) if best_iteration is not None else None,
            },
            "canonicalConfig": canonical.to_manifest(),
            "researchExperimentId": experiment_spec.experiment_id,
            "researchExperiment": experiment_spec.to_manifest(),
            "phase2Hypothesis": experiment_spec.hypothesis_manifest(),
            "predictionSnapshot": prediction_snapshot,
            "portfolioPolicySha256": sha256_json(canonical.to_manifest()["portfolio"]),
            "lineage": lineage,
            "promotion": {
                "status": (
                    PromotionStatus.PROMOTED.value
                    if promoted
                    else PromotionStatus.CANDIDATE.value
                    if gate_passed or review_required
                    else PromotionStatus.REJECTED.value
                ),
                "decision": gate_report["decision"],
                "gateReportPath": str(gate_path),
                "gateMode": (
                    "component_validation"
                    if promotion_mode == "component"
                    else "final_holdout"
                    if promotion_mode == "holdout"
                    else "release"
                ),
                "promotionAuthorized": _promotion_authorized(promotion_mode, promoted),
            },
            "runtime": runtime.to_manifest(),
            "artifactLevel": artifact_level,
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
                {"name": pred_snapshot_path.name, "localPath": str(pred_snapshot_path)},
                {"name": label_path.name, "localPath": str(label_path), "rows": len(label_frame)},
                {"name": report_path.name, "localPath": str(report_path), "rows": len(report)},
                {"name": audit_path.name, "localPath": str(audit_path), "rows": len(audit)},
                {"name": holdings_path.name, "localPath": str(holdings_path), "rows": len(holdings)},
                {"name": timings_path.name, "localPath": str(timings_path)},
                {"name": gate_path.name, "localPath": str(gate_path)},
                *report_artifacts,
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
        from .dataset_registry import DatasetRegistry

        DatasetRegistry(settings.registry_path).register_research_manifest(manifest_path)
        if artifact_level == "full":
            with timings.measure("report_seconds"):
                write_backtest_report(settings, artifact_dir)
        timing_payload = timings.to_dict()
        manifest["timings"] = timing_payload
        write_timings(timings_path, runtime, timing_payload)
        recorder.log_metrics(
            **{
                "timing.report_seconds": float(timing_payload["phasesSeconds"].get("report_seconds", 0.0)),
                "timing.wall_seconds": float(timing_payload["wallSeconds"]),
                "timing.peak_rss_mb": float(timing_payload.get("peakRssMb") or 0.0),
            }
        )
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        if not gate_passed:
            if review_required or promotion_mode == "holdout":
                return manifest_path
            raise ResearchPromotionError(manifest_path)
        if promotion_mode in {"component", "holdout"} or not promoted:
            return manifest_path
        assert path is not None
        return path
