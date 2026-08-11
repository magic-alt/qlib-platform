from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

import pandas as pd

from .canonical_config import StrategySpec
from .lineage import sha256_json
from .model_runtime import StageTimings
from .runtime_safety import resolve_qlib_parallel_runtime
from .settings import Settings
from .store import sha256_file


@dataclass(frozen=True)
class MarketDataView:
    quote: pd.DataFrame


def _load_predictions(path: str | Path) -> pd.DataFrame:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"prediction artifact not found: {source}")
    frame = pd.read_parquet(source)
    if isinstance(frame, pd.Series):
        frame = frame.to_frame("score")
    if not isinstance(frame.index, pd.MultiIndex) or not {
        "datetime",
        "instrument",
    }.issubset(frame.index.names):
        raise ValueError("predictions must use a datetime/instrument MultiIndex")
    if "score" not in frame:
        if len(frame.columns) != 1:
            raise ValueError("predictions must contain a score column")
        frame = frame.rename(columns={frame.columns[0]: "score"})
    frame = frame[["score"]].sort_index()
    if frame.empty or frame["score"].isna().all():
        raise ValueError("prediction artifact contains no usable scores")
    return frame


def _market_data_view(settings: Settings, score: pd.Series, timings: StageTimings) -> MarketDataView:
    from qlib.data import D

    from .train_select import _official_calendar

    dates = pd.DatetimeIndex(score.index.get_level_values("datetime").unique()).sort_values()
    calendar = _official_calendar(settings)
    trade_dates = pd.DatetimeIndex(
        [calendar[calendar > date][0] for date in dates if len(calendar[calendar > date])]
    )
    if trade_dates.empty:
        empty = pd.DataFrame(columns=["trade_date", "instrument", "paused", "is_limit_up", "is_limit_down"])
        return MarketDataView(empty)
    instruments = sorted(score.index.get_level_values("instrument").astype(str).unique())
    with timings.measure("audit_quote_query_seconds"):
        raw = D.features(
            instruments,
            ["$close", "$is_limit_up", "$is_limit_down"],
            start_time=trade_dates.min(),
            end_time=trade_dates.max(),
            freq="day",
        )
    if raw.empty:
        raise RuntimeError("cannot audit TopkDropout without Qlib trade-status fields")
    with timings.measure("audit_quote_transform_seconds"):
        frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
        frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.normalize()
        frame["paused"] = pd.to_numeric(frame["$close"], errors="coerce").isna().astype(float)
        for column in ("is_limit_up", "is_limit_down"):
            frame[column] = pd.to_numeric(frame[f"${column}"], errors="coerce").fillna(1.0)
        quote = frame.loc[
            frame["trade_date"].isin(trade_dates),
            ["trade_date", "instrument", "paused", "is_limit_up", "is_limit_down"],
        ]
    return MarketDataView(quote=quote)


def _portfolio_config(
    settings: Settings,
    *,
    policy: object,
    benchmark: object,
    start_time: str,
    end_time: str,
) -> dict[str, object]:
    research = settings.data.get("research", {})
    research = research if isinstance(research, Mapping) else {}
    return {
        "strategy": {
            "class": "TopkDropoutStrategy",
            "module_path": "qlib.contrib.strategy",
            "kwargs": {"signal": "<PRED>", **policy.__dict__},
        },
        "executor": {
            "class": "SimulatorExecutor",
            "module_path": "qlib.backtest.executor",
            "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True},
        },
        "backtest": {
            "start_time": start_time,
            "end_time": end_time,
            "account": int(research.get("backtest_account", 500_000)),
            "benchmark": benchmark,
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
    }


def backtest_predictions(
    settings: Settings,
    predictions: str | Path,
    *,
    benchmark: str | None = None,
    topn: int | None = None,
    artifact_level: str = "full",
) -> Path:
    """Run portfolio construction from immutable OOS predictions without fitting a model."""

    if artifact_level not in {"minimal", "full"}:
        raise ValueError("artifact_level must be 'minimal' or 'full'")
    source_path = Path(predictions).expanduser().resolve()
    pred = _load_predictions(source_path)
    source_sha = sha256_file(source_path)
    timings = StageTimings()

    try:
        import qlib
        from qlib.constant import REG_CN
        from qlib.workflow import R
        from qlib.workflow.record_temp import PortAnaRecord
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("Qlib is required for predictions-only backtests") from exc

    from .backtest_report import ReportArtifacts, export_holding_snapshots, write_backtest_report
    from .strategy_audit import build_strategy_audit
    from .train_select import _configure_mlflow_tracking, _dataset_id, _resolve_benchmark

    class PredictionsPortAnaRecord(PortAnaRecord):
        # Qlib's PortAnaRecord declares SignalRecord as its dependency, whose
        # check requires both pred.pkl and label.pkl.  A predictions-only
        # portfolio legitimately has no labels, and PortAnaRecord itself only
        # consumes pred.pkl, so disable that overly broad dependency check.
        depend_cls = None

        def load(self, name: str, parents: bool = True):
            if name == "pred.pkl":
                return self.recorder.load_object(name)
            return super().load(name, parents=parents)

    parallel = resolve_qlib_parallel_runtime(settings)
    with timings.measure("qlib_init_seconds"):
        qlib.init(
            provider_uri=str(settings.qlib_data_uri),
            region=REG_CN,
            expression_cache=None,
            dataset_cache=None,
            **parallel.qlib_init_kwargs(),
        )
    dates = pd.DatetimeIndex(pred.index.get_level_values("datetime"))
    start_time = str(dates.min().date())
    end_time = str(dates.max().date())
    with timings.measure("benchmark_load_seconds"):
        benchmark_data = _resolve_benchmark(settings, benchmark, start_time, end_time)
    strategy = StrategySpec.from_settings(settings, topk_override=topn)
    policy = strategy.to_policy()
    portfolio_identity = {
        "sourcePredictionsSha256": source_sha,
        "datasetFingerprint": _dataset_id(settings),
        "strategy": asdict(strategy),
        "benchmark": benchmark or "SH000300",
        "benchmarkSha256": (
            sha256_file(settings.paths.metadata / "benchmarks" / "SH000300.parquet")
            if (settings.paths.metadata / "benchmarks" / "SH000300.parquet").is_file()
            else None
        ),
        "researchBacktest": _portfolio_config(
            settings,
            policy=policy,
            benchmark="<LOCAL_SERIES>",
            start_time=start_time,
            end_time=end_time,
        )["backtest"],
    }
    portfolio_fingerprint = sha256_json(portfolio_identity)[:24]
    _configure_mlflow_tracking(settings)
    tracking_uri = os.environ["MLFLOW_TRACKING_URI"]
    with R.start(experiment_name="predictions_only_portfolio", uri=tracking_uri):
        recorder = R.get_recorder()
        recorder.log_params(
            source_predictions_sha256=source_sha,
            portfolio_fingerprint=portfolio_fingerprint,
            artifact_level=artifact_level,
        )
        recorder.save_objects(**{"pred.pkl": pred})
        with timings.measure("portfolio_engine_seconds"):
            PredictionsPortAnaRecord(
                recorder=recorder,
                config=_portfolio_config(
                    settings,
                    policy=policy,
                    benchmark=benchmark_data,
                    start_time=start_time,
                    end_time=end_time,
                ),
            ).generate()

        run_id = str(getattr(recorder, "id", portfolio_fingerprint))
        output = settings.paths.output / "research" / run_id
        output.mkdir(parents=True, exist_ok=True)
        pred_path = output / "oos_predictions.parquet"
        report_path = output / "portfolio_report.parquet"
        holdings_path = output / "holdings.parquet"
        audit_path = output / "strategy_audit.parquet"
        timings_path = output / "timings.json"
        manifest_path = output / "manifest.json"
        with timings.measure("prediction_export_seconds"):
            pred.to_parquet(pred_path)
        with timings.measure("portfolio_artifact_load_seconds"):
            report = recorder.load_object("portfolio_analysis/report_normal_1day.pkl")
            positions = recorder.load_object("portfolio_analysis/positions_normal_1day.pkl")
            indicators = recorder.load_object("portfolio_analysis/indicators_normal_1day_obj.pkl")
        report.to_parquet(report_path)
        with timings.measure("holdings_export_seconds"):
            holdings = export_holding_snapshots(positions)
            holdings.to_parquet(holdings_path, index=False)
        market_data = _market_data_view(settings, pred["score"], timings)
        with timings.measure("audit_build_seconds"):
            audit = build_strategy_audit(
                pred["score"], positions, indicators, market_data.quote, policy=policy
            )
        with timings.measure("audit_export_seconds"):
            audit.to_parquet(audit_path, index=False)
        metrics: dict[str, float] = {}
        for column in ("return", "bench", "cost"):
            if column in report:
                values = pd.to_numeric(report[column], errors="coerce").dropna()
                metrics[f"{column}Total"] = float((1.0 + values).prod() - 1.0)
        timing_payload = timings.to_dict()
        artifacts = [
            {"name": pred_path.name, "localPath": str(pred_path), "rows": len(pred)},
            {"name": report_path.name, "localPath": str(report_path), "rows": len(report)},
            {"name": holdings_path.name, "localPath": str(holdings_path), "rows": len(holdings)},
            {"name": audit_path.name, "localPath": str(audit_path), "rows": len(audit)},
            {"name": timings_path.name, "localPath": str(timings_path)},
        ]
        if artifact_level == "full":
            artifacts.extend(
                ReportArtifacts(
                    markdown_path=output / "backtest_report.md",
                    pdf_path=output / "backtest_report.pdf",
                    assets_dir=output / "report_assets",
                ).manifest_entries()
            )
        manifest = {
            "schemaVersion": "2.0",
            "externalRunId": run_id,
            "runKind": "predictions_only_backtest",
            "name": f"Predictions-only portfolio {start_time}..{end_time}",
            "dataset": {"fingerprint": _dataset_id(settings), "startDate": start_time, "endDate": end_time},
            "sourcePrediction": {
                "localPath": str(source_path),
                "sha256": source_sha,
            },
            "strategy": asdict(strategy),
            "strategyFingerprint": sha256_json(asdict(strategy))[:24],
            "portfolioFingerprint": portfolio_fingerprint,
            "execution": {
                "benchmark": benchmark or "SH000300",
                "dealPrice": _portfolio_config(
                    settings,
                    policy=policy,
                    benchmark="<LOCAL_SERIES>",
                    start_time=start_time,
                    end_time=end_time,
                )["backtest"]["exchange_kwargs"]["deal_price"],
                "topkDropout": asdict(strategy),
            },
            "promotion": {
                "status": "RESEARCH_ONLY",
                "decision": "NOT_EVALUATED",
                "gateMode": "portfolio_only",
                "promotionAuthorized": False,
            },
            "artifactLevel": artifact_level,
            "runtime": {
                "qlibKernels": parallel.kernels,
                "joblibBackend": parallel.joblib_backend,
            },
            "timings": timing_payload,
            "metrics": metrics,
            "artifacts": artifacts,
        }
        timings_path.write_text(json.dumps(timing_payload, indent=2), encoding="utf-8")
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        if artifact_level == "full":
            with timings.measure("report_seconds"):
                write_backtest_report(settings, output)
            timing_payload = timings.to_dict()
            manifest["timings"] = timing_payload
            timings_path.write_text(json.dumps(timing_payload, indent=2), encoding="utf-8")
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        recorder.log_metrics(
            **{f"timing.{key}": float(value) for key, value in timings.to_dict()["phasesSeconds"].items()}
        )
    return manifest_path
