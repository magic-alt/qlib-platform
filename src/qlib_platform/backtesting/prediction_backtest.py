from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, cast

import pandas as pd

from qlib_platform.canonical_config import DatasetSpec, StrategySpec
from qlib_platform.datasets.dataset_resolver import ResolvedDataset, pin_dataset
from qlib_platform.lineage import sha256_json
from qlib_platform.models.model_runtime import StageTimings
from qlib_platform.artifacts.prediction_snapshot import load_prediction_snapshot, prediction_snapshot_path
from qlib_platform.runtime.runtime_safety import resolve_qlib_parallel_runtime
from qlib_platform.settings import Settings
from qlib_platform.data.store import sha256_file


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

    from qlib_platform.research.train_select import _official_calendar

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
    from qlib_platform.backtesting.strategy_factory import build_qlib_strategy_config, resolve_strategy_policy

    resolved = resolve_strategy_policy(policy)
    research = settings.data.get("research", {})
    research = research if isinstance(research, Mapping) else {}
    return {
        "strategy": build_qlib_strategy_config(resolved),
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


def _validate_snapshot_data_release(
    settings: Settings,
    pinned_dataset: ResolvedDataset,
    snapshot: Mapping[str, Any],
) -> None:
    contract = snapshot.get("contract")
    if not isinstance(contract, Mapping):
        raise ValueError("PredictionSnapshot contract is missing")
    snapshot_release = str(contract.get("data_release_id") or "").strip()
    expected_release = DatasetSpec.from_settings(settings).dataset_id
    if snapshot_release != expected_release:
        raise ValueError(
            "PredictionSnapshot DataRelease does not match pinned DataRelease: "
            f"snapshot={snapshot_release!r}, pinned={expected_release!r}"
        )

    manifest_path = pinned_dataset.manifest_path
    if not manifest_path.is_file():
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    semantic_contract = manifest.get("semantic_contract", {})
    materialized_release = (
        str(semantic_contract.get("data_release_id") or "").strip()
        if isinstance(semantic_contract, Mapping)
        else ""
    )
    if materialized_release and snapshot_release != materialized_release:
        raise ValueError(
            "PredictionSnapshot DataRelease does not match pinned DataRelease: "
            f"snapshot={snapshot_release!r}, materialized={materialized_release!r}"
        )


def backtest_predictions(
    settings: Settings,
    predictions: str | Path,
    *,
    benchmark: str | None = None,
    topn: int | None = None,
    n_drop: int | None = None,
    hold_thresh: int | None = None,
    artifact_level: str = "full",
) -> Path:
    """Run portfolio construction from immutable OOS predictions without fitting a model."""

    settings, pinned_dataset = pin_dataset(settings)
    if artifact_level not in {"minimal", "full"}:
        raise ValueError("artifact_level must be 'minimal' or 'full'")
    source_reference = Path(predictions).expanduser().resolve()
    source_snapshot: dict[str, Any] | None = None
    if source_reference.suffix == ".json" or prediction_snapshot_path(source_reference).is_file():
        pred, source_snapshot = load_prediction_snapshot(source_reference)
        _validate_snapshot_data_release(settings, pinned_dataset, source_snapshot)
        pred = pred[["score"]]
        payload = source_snapshot["payload"]
        snapshot_manifest = (
            source_reference
            if source_reference.suffix == ".json"
            else prediction_snapshot_path(source_reference)
        )
        source_path = (snapshot_manifest.parent / str(payload["path"])).resolve()
    else:
        source_path = source_reference
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

    from qlib_platform.backtesting.backtest_report import ReportArtifacts, export_holding_snapshots, write_backtest_report
    from qlib_platform.backtesting.strategy_audit import build_strategy_audit
    from qlib_platform.research.train_select import _configure_mlflow_tracking, _dataset_id, _resolve_benchmark

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
    strategy = StrategySpec.from_settings(
        settings,
        topk_override=topn,
        n_drop_override=n_drop,
        hold_thresh_override=hold_thresh,
    )
    policy = strategy.to_policy()
    portfolio_identity = {
        "sourcePredictionsSha256": source_sha,
        "datasetFingerprint": _dataset_id(settings),
        "strategy": asdict(strategy.to_policy()),
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
            from qlib_platform.backtesting.topk_dropout import enforce_deterministic_qlib_position_order

            enforce_deterministic_qlib_position_order()
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
            "dataset": {
                "fingerprint": _dataset_id(settings),
                "versionId": pinned_dataset.version_id,
                "startDate": start_time,
                "endDate": end_time,
            },
            "sourcePrediction": {
                "localPath": str(source_path),
                "sha256": source_sha,
                "snapshotId": source_snapshot.get("snapshotId") if source_snapshot else None,
                "snapshotContract": source_snapshot.get("contract") if source_snapshot else None,
            },
            "strategy": {"policy": strategy.policy, **asdict(strategy.to_policy())},
            "strategyFingerprint": sha256_json(asdict(strategy.to_policy()))[:24],
            "portfolioFingerprint": portfolio_fingerprint,
            "execution": {
                "benchmark": benchmark or "SH000300",
                "dealPrice": cast(
                    Any,
                    _portfolio_config(
                        settings,
                        policy=policy,
                        benchmark="<LOCAL_SERIES>",
                        start_time=start_time,
                        end_time=end_time,
                    ),
                )["backtest"]["exchange_kwargs"]["deal_price"],
                "strategyPolicy": strategy.policy,
                **(
                    {"rankBuffer": asdict(strategy.to_policy())}
                    if strategy.policy == "rank_buffer_v1"
                    else {"topkDropout": asdict(strategy.to_policy())}
                ),
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
            "executionIsolation": {
                "featureComputeCalls": 0,
                "rawMaterializationCalls": 0,
                "modelTrainCalls": 0,
                "modelPredictCalls": 0,
            },
            "timings": timing_payload,
            "metrics": metrics,
            "artifacts": artifacts,
        }
        timings_path.write_text(json.dumps(timing_payload, indent=2), encoding="utf-8")
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        if artifact_level == "full":
            from qlib_platform.research.p0_baseline import write_p0_artifacts

            write_p0_artifacts(output)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            with timings.measure("report_seconds"):
                write_backtest_report(settings, output)
            timing_payload = timings.to_dict()
            manifest["timings"] = timing_payload
            timings_path.write_text(json.dumps(timing_payload, indent=2), encoding="utf-8")
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        recorder.log_metrics(
            **{f"timing.{key}": float(value) for key, value in timings.to_dict()["phasesSeconds"].items()}
        )
    from qlib_platform.datasets.dataset_registry import DatasetRegistry

    DatasetRegistry(settings.registry_path).register_research_manifest(manifest_path)
    return manifest_path
