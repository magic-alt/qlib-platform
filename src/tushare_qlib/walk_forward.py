from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from .settings import Settings
from .model_runtime import load_model_profile, resolve_runtime, write_timings
from .research_gate import (
    ResearchPromotionError,
    ResearchThresholds,
    derive_research_metrics,
    evaluate_research_metrics,
    write_gate_report,
)
from .store import sha256_file
from .train_select import train_backtest_select


@dataclass(frozen=True)
class Fold:
    key: str
    train: tuple[str, str]
    valid: tuple[str, str]
    test: tuple[str, str]
    final_holdout: bool = False


def _artifact_path(manifest: dict[str, Any], name: str) -> Path:
    for item in manifest.get("artifacts", []):
        if isinstance(item, dict) and item.get("name") == name and item.get("localPath"):
            return Path(str(item["localPath"]))
    raise FileNotFoundError(f"manifest artifact is missing: {name}")


def _rebase_reports(reports: list[tuple[str, pd.DataFrame]]) -> pd.DataFrame:
    """Chain independent walk-forward folds into one account-value series.

    Each Qlib fold starts with the configured account.  Rebuilding account,
    cash and market value from daily returns avoids showing a false reset at
    each fold boundary while preserving the source fold artifacts separately.
    """

    if not reports:
        raise ValueError("at least one walk-forward report is required")
    equity = float(pd.to_numeric(reports[0][1]["account"], errors="raise").iloc[0])
    total_cost = 0.0
    total_turnover = 0.0
    frames: list[pd.DataFrame] = []
    for key, source in reports:
        frame = source.copy().sort_index()
        account = pd.to_numeric(frame["account"], errors="raise")
        returns = pd.to_numeric(frame.get("return", account.pct_change()), errors="coerce").fillna(0.0)
        cash_source = frame["cash"] if "cash" in frame else pd.Series(0.0, index=frame.index)
        cash = pd.to_numeric(cash_source, errors="coerce").fillna(0.0)
        value_source = frame["value"] if "value" in frame else account - cash
        value = pd.to_numeric(value_source, errors="coerce").fillna(account - cash)
        cost_source = frame["total_cost"] if "total_cost" in frame else pd.Series(0.0, index=frame.index)
        turnover_source = (
            frame["total_turnover"] if "total_turnover" in frame else pd.Series(0.0, index=frame.index)
        )
        source_cost = pd.to_numeric(cost_source, errors="coerce").fillna(0.0)
        source_turnover = pd.to_numeric(turnover_source, errors="coerce").fillna(0.0)
        daily_cost = source_cost.diff().fillna(source_cost)
        daily_turnover = source_turnover.diff().fillna(source_turnover)
        rows: list[pd.Series] = []
        for position, (_, row) in enumerate(frame.iterrows()):
            equity *= 1.0 + float(returns.iloc[position])
            scale = equity / float(account.iloc[position]) if account.iloc[position] else 1.0
            row = row.copy()
            row["account"] = equity
            row["cash"] = float(cash.iloc[position]) * scale
            row["value"] = float(value.iloc[position]) * scale
            total_cost += float(daily_cost.iloc[position]) * scale
            total_turnover += float(daily_turnover.iloc[position]) * scale
            row["total_cost"] = total_cost
            row["total_turnover"] = total_turnover
            row["fold_key"] = key
            rows.append(row)
        rebased = pd.DataFrame(rows, index=frame.index)
        rebased.index.name = frame.index.name
        frames.append(rebased)
    return pd.concat(frames).sort_index()


def _aggregate_component_timings(manifests: list[dict[str, Any]]) -> dict[str, float]:
    aggregate: dict[str, float] = {}
    for manifest in manifests:
        timings = manifest.get("timings", {})
        phases = timings.get("phasesSeconds", {}) if isinstance(timings, dict) else {}
        if not isinstance(phases, dict):
            continue
        for key, value in phases.items():
            aggregate[str(key)] = aggregate.get(str(key), 0.0) + float(value)
    return {key: round(value, 6) for key, value in aggregate.items()}


def _checkpoint_fingerprint(
    settings: Settings,
    fold: Fold,
    *,
    runtime_fingerprint: str,
    benchmark: str,
    topn: int | None,
) -> str:
    payload = {
        "runtimeFingerprint": runtime_fingerprint,
        "fold": asdict(fold),
        "benchmark": benchmark,
        "topn": topn,
        "research": settings.data.get("research", {}),
        "universe": settings.data.get("universe", {}),
        "datasetUri": str(settings.qlib_data_uri),
        "promotionContract": "release-v2" if fold.final_holdout else "component-validation-v2",
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


def _at_or_after(calendar: pd.DatetimeIndex, value: pd.Timestamp) -> pd.Timestamp:
    found = calendar[calendar >= value]
    if found.empty:
        raise ValueError(f"no trading day at or after {value.date()}")
    return found[0]


def _at_or_before(calendar: pd.DatetimeIndex, value: pd.Timestamp) -> pd.Timestamp:
    found = calendar[calendar <= value]
    if found.empty:
        raise ValueError(f"no trading day at or before {value.date()}")
    return found[-1]


def build_walk_forward_plan(
    calendar: pd.DatetimeIndex,
    start_date: str,
    end_date: str,
    *,
    train_years: int = 5,
    valid_months: int = 6,
    test_months: int = 3,
    holdout_months: int = 12,
    purge_days: int = 5,
    embargo_days: int = 5,
    min_holdout_observations: int = 252,
) -> list[Fold]:
    dates = calendar[(calendar >= pd.Timestamp(start_date)) & (calendar <= pd.Timestamp(end_date))]
    if len(dates) < 252 * 6:
        raise ValueError("walk-forward requires at least six years of trading dates")
    calendar_holdout_start = _at_or_after(
        dates, dates[-1] - pd.DateOffset(months=holdout_months) + pd.Timedelta(days=1)
    )
    if min_holdout_observations <= 0 or len(dates) <= min_holdout_observations:
        raise ValueError("min_holdout_observations must fit inside the walk-forward calendar")
    observation_holdout_start = dates[-(min_holdout_observations + 1)]
    holdout_start = min(calendar_holdout_start, observation_holdout_start)
    test_start = _at_or_after(dates, dates[0] + pd.DateOffset(years=train_years, months=valid_months))
    folds: list[Fold] = []
    index = 0
    while test_start < holdout_start:
        test_end = _at_or_before(
            dates,
            min(
                test_start + pd.DateOffset(months=test_months) - pd.Timedelta(days=1),
                holdout_start - pd.Timedelta(days=1),
            ),
        )
        valid_end_pos = dates.get_loc(test_start) - embargo_days - 1
        valid_end = dates[valid_end_pos]
        valid_start = _at_or_after(
            dates, valid_end - pd.DateOffset(months=valid_months) + pd.Timedelta(days=1)
        )
        train_end = dates[dates.get_loc(valid_start) - purge_days - 1]
        train_start = _at_or_after(dates, train_end - pd.DateOffset(years=train_years) + pd.Timedelta(days=1))
        folds.append(
            Fold(
                f"rolling_{index:02d}",
                (str(train_start.date()), str(train_end.date())),
                (str(valid_start.date()), str(valid_end.date())),
                (str(test_start.date()), str(test_end.date())),
            )
        )
        index += 1
        test_start = _at_or_after(dates, test_end + pd.Timedelta(days=1))
    valid_end = dates[dates.get_loc(holdout_start) - embargo_days - 1]
    valid_start = _at_or_after(dates, valid_end - pd.DateOffset(months=valid_months) + pd.Timedelta(days=1))
    train_end = dates[dates.get_loc(valid_start) - purge_days - 1]
    train_start = _at_or_after(dates, train_end - pd.DateOffset(years=train_years) + pd.Timedelta(days=1))
    folds.append(
        Fold(
            "final_holdout",
            (str(train_start.date()), str(train_end.date())),
            (str(valid_start.date()), str(valid_end.date())),
            (str(holdout_start.date()), str(dates[-2].date())),
            True,
        )
    )
    return folds


def _evaluate_aggregate_oos_gate(
    settings: Settings,
    manifests: list[dict[str, Any]],
    reports: list[tuple[str, pd.DataFrame]],
    gate_path: Path,
) -> dict[str, object]:
    """Apply the release gate once to the combined rolling-fold OOS evidence."""

    if not manifests or not reports:
        raise ValueError("aggregate OOS gate requires rolling component evidence")
    prediction_paths = [_artifact_path(manifest, "oos_predictions.parquet") for manifest in manifests]
    label_paths = [_artifact_path(manifest, "oos_labels.parquet") for manifest in manifests]
    predictions = pd.concat([pd.read_parquet(path) for path in prediction_paths]).sort_index()
    labels = pd.concat([pd.read_parquet(path) for path in label_paths]).sort_index()
    combined_report = _rebase_reports(reports)
    lineage_complete = all(bool(manifest.get("lineage", {}).get("complete")) for manifest in manifests)
    dirty_research_override = not lineage_complete and all(
        bool(manifest.get("lineage", {}).get("complete"))
        or bool(manifest.get("metrics", {}).get("dirty_research_override"))
        for manifest in manifests
    )
    unique_artifact = len({sha256_file(path) for path in prediction_paths}) == len(prediction_paths)
    metrics = derive_research_metrics(
        predictions,
        labels,
        combined_report,
        unique_artifact=unique_artifact,
        lineage_complete=lineage_complete,
    )
    research = settings.data.get("research", {})
    thresholds = ResearchThresholds.from_mapping(
        research.get("promotion_thresholds", {}) if isinstance(research, dict) else {}
    )
    metrics["dirty_research_override"] = dirty_research_override
    report = evaluate_research_metrics(metrics, thresholds, allow_dirty_research=dirty_research_override)
    if dirty_research_override and report["passed"]:
        report["decision"] = "RESEARCH_ONLY"
    report["gate_mode"] = "aggregate_rolling_oos"
    report["component_count"] = len(manifests)
    write_gate_report(report, gate_path)
    return report


def run_walk_forward(
    settings: Settings,
    *,
    start_date: str,
    end_date: str,
    benchmark: str = "SH000300",
    topn: int | None = None,
    model_profile: str | Path | None = None,
) -> Path:
    orchestration_started = time.perf_counter()
    runtime = resolve_runtime(load_model_profile(settings, model_profile))
    calendar_frame = pd.read_parquet(settings.paths.metadata / "trade_calendar.parquet")
    calendar = pd.DatetimeIndex(
        pd.to_datetime(
            calendar_frame.loc[pd.to_numeric(calendar_frame["is_open"], errors="coerce") == 1, "cal_date"]
        )
        .dropna()
        .sort_values()
        .unique()
    )
    research = settings.data.get("research", {})
    thresholds = ResearchThresholds.from_mapping(
        research.get("promotion_thresholds", {}) if isinstance(research, dict) else {}
    )
    label_buffer_days = (
        int(research.get("walk_forward_label_buffer_days", 5)) if isinstance(research, dict) else 5
    )
    if label_buffer_days < 0:
        raise ValueError("research.walk_forward_label_buffer_days must be non-negative")
    folds = build_walk_forward_plan(
        calendar,
        start_date,
        end_date,
        min_holdout_observations=thresholds.min_observations + label_buffer_days,
    )
    run_root = settings.paths.output / "research" / "walk_forward"
    run_root.mkdir(parents=True, exist_ok=True)
    manifests: list[dict[str, Any]] = []
    reports: list[tuple[str, pd.DataFrame]] = []
    audits: list[pd.DataFrame] = []
    holdings: list[pd.DataFrame] = []
    component_runs: list[dict[str, Any]] = []

    def execute_fold(
        fold: Fold,
    ) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
        checkpoint_fingerprint = _checkpoint_fingerprint(
            settings,
            fold,
            runtime_fingerprint=runtime.fingerprint,
            benchmark=benchmark,
            topn=topn,
        )
        checkpoint = run_root / f"{fold.key}_{checkpoint_fingerprint}.json"
        reused = checkpoint.exists()
        if checkpoint.exists():
            fold_manifest_path = Path(json.loads(checkpoint.read_text(encoding="utf-8"))["manifest"])
        else:
            result_path = train_backtest_select(
                settings,
                train=fold.train,
                valid=fold.valid,
                test=fold.test,
                benchmark=benchmark,
                topn=topn,
                experiment_name=f"lean_csi300_{runtime.profile.name}_{fold.key}",
                run_kind="final_holdout" if fold.final_holdout else "walk_forward_fold",
                runtime=runtime,
                promotion_mode="release" if fold.final_holdout else "component",
            )
            if fold.final_holdout and result_path.name != "manifest.json":
                model_id = str(pd.read_csv(result_path)["model_id"].iloc[0])
                fold_manifest_path = settings.paths.output / "research" / model_id / "manifest.json"
            else:
                fold_manifest_path = result_path
            checkpoint.write_text(
                json.dumps(
                    {
                        "manifest": str(fold_manifest_path),
                        "runtimeFingerprint": runtime.fingerprint,
                        "checkpointFingerprint": checkpoint_fingerprint,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        manifest = json.loads(fold_manifest_path.read_text(encoding="utf-8"))
        promotion = manifest.get("promotion", {})
        if not isinstance(promotion, dict):
            raise ValueError(f"fold manifest has no promotion contract: {fold.key}")
        expected_status = "PROMOTED" if fold.final_holdout else "CANDIDATE"
        expected_mode = "release" if fold.final_holdout else "component_validation"
        dirty_candidate = (
            fold.final_holdout
            and promotion.get("status") == "CANDIDATE"
            and bool(manifest.get("metrics", {}).get("dirty_research_override"))
        )
        if (promotion.get("status") != expected_status and not dirty_candidate) or promotion.get(
            "gateMode"
        ) != expected_mode:
            raise ValueError(
                f"fold {fold.key} has incompatible promotion contract: "
                f"status={promotion.get('status')}, gateMode={promotion.get('gateMode')}"
            )
        report = pd.read_parquet(_artifact_path(manifest, "portfolio_report.parquet"))
        audit = pd.read_parquet(_artifact_path(manifest, "strategy_audit.parquet"))
        holding = pd.read_parquet(_artifact_path(manifest, "holdings.parquet"))
        component_run = {
            "key": fold.key,
            "externalRunId": str(manifest["externalRunId"]),
            "manifestPath": str(fold_manifest_path),
            "checkpointReused": reused,
            "promotionMode": "release" if fold.final_holdout else "component_validation",
            "timings": manifest.get("timings", {}),
        }
        return manifest, report, audit, holding, component_run

    rolling_folds = [fold for fold in folds if not fold.final_holdout]
    final_folds = [fold for fold in folds if fold.final_holdout]
    if len(final_folds) != 1:
        raise ValueError("walk-forward plan must contain exactly one final holdout")
    for fold in rolling_folds:
        manifest, report, audit, holding, component_run = execute_fold(fold)
        if reports and pd.Timestamp(report.index.min()) <= pd.Timestamp(reports[-1][1].index.max()):
            raise ValueError(f"overlapping OOS reports at fold {fold.key}")
        manifests.append(manifest)
        reports.append((fold.key, report))
        audits.append(audit.assign(fold_key=fold.key))
        holdings.append(holding.assign(fold_key=fold.key))
        component_runs.append(component_run)

    rolling_ids = [str(manifest["externalRunId"]) for manifest in manifests]
    aggregate_key = hashlib.sha256("|".join(rolling_ids).encode()).hexdigest()[:32]
    aggregate_gate_path = run_root / f"aggregate_oos_gate_{aggregate_key}.json"
    aggregate_gate = _evaluate_aggregate_oos_gate(settings, manifests, reports, aggregate_gate_path)
    if not aggregate_gate["passed"]:
        rejection_manifest = run_root / f"aggregate_oos_{aggregate_key}.manifest.json"
        rejection_manifest.write_text(
            json.dumps(
                {
                    "schemaVersion": "2.0",
                    "externalRunId": aggregate_key,
                    "runKind": "walk_forward_aggregate_oos",
                    "promotion": {
                        "status": "REJECTED",
                        "decision": "REJECT",
                        "gateMode": "aggregate_rolling_oos",
                        "gateReportPath": str(aggregate_gate_path),
                    },
                    "componentRuns": component_runs,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        raise ResearchPromotionError(rejection_manifest)

    final_fold = final_folds[0]
    final_manifest, final_report, final_audit, final_holding, final_run = execute_fold(final_fold)
    if pd.Timestamp(final_report.index.min()) <= pd.Timestamp(reports[-1][1].index.max()):
        raise ValueError(f"overlapping OOS reports at fold {final_fold.key}")
    manifests.append(final_manifest)
    reports.append((final_fold.key, final_report))
    audits.append(final_audit.assign(fold_key=final_fold.key))
    holdings.append(final_holding.assign(fold_key=final_fold.key))
    component_runs.append(final_run)

    combined = _rebase_reports(reports)
    combined_audit = pd.concat(audits, ignore_index=True)
    combined_holdings = pd.concat(holdings, ignore_index=True)
    fold_ids = [item["externalRunId"] for item in manifests]
    external_id = hashlib.sha256("|".join(fold_ids).encode()).hexdigest()[:32]
    output_dir = settings.paths.output / "research" / external_id
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "portfolio_report.parquet"
    audit_path = output_dir / "strategy_audit.parquet"
    holdings_path = output_dir / "holdings.parquet"
    timings_path = output_dir / "timings.json"
    combined.to_parquet(report_path)
    combined_audit.to_parquet(audit_path, index=False)
    combined_holdings.to_parquet(holdings_path, index=False)
    metrics: dict[str, float] = {}
    for column in ("return", "bench", "cost"):
        if column in combined:
            values = pd.to_numeric(combined[column], errors="coerce").dropna()
            metrics[f"{column}Total"] = float((1.0 + values).prod() - 1.0)
    component_lineage = [manifest.get("lineage", {}) for manifest in manifests]
    component_lineage_ids = [
        str(item.get("lineageId")) for item in component_lineage if isinstance(item, dict)
    ]
    aggregate_lineage = {
        "componentLineageIds": component_lineage_ids,
        "complete": len(component_lineage_ids) == len(manifests)
        and all(bool(item.get("complete")) for item in component_lineage if isinstance(item, dict)),
        "qlibPlatformCommit": component_lineage[-1].get("qlibPlatformCommit"),
        "qlibCommit": component_lineage[-1].get("qlibCommit"),
        "datasetFingerprint": manifests[-1]["dataset"].get("fingerprint"),
    }
    aggregate_lineage["lineageId"] = hashlib.sha256(
        json.dumps(aggregate_lineage, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:32]
    promoted = (
        bool(aggregate_lineage["complete"])
        and all(
            manifest.get("promotion", {}).get("status") in {"CANDIDATE", "PROMOTED"}
            for manifest in manifests[:-1]
        )
        and final_manifest.get("promotion", {}).get("status") == "PROMOTED"
    )
    aggregate_phases = _aggregate_component_timings(manifests)
    timings = {
        "clock": "time.perf_counter",
        "phasesSeconds": aggregate_phases,
        "totalSeconds": round(sum(aggregate_phases.values()), 6),
        "orchestrationWallSeconds": round(time.perf_counter() - orchestration_started, 6),
        "checkpointReuseCount": sum(bool(item["checkpointReused"]) for item in component_runs),
        "reportRenderingIncluded": False,
    }
    write_timings(timings_path, runtime, timings)
    payload = {
        "schemaVersion": "2.0",
        "externalRunId": external_id,
        "runKind": "walk_forward",
        "name": f"Qlib CSI300 walk-forward {start_date}..{end_date}",
        "dataset": manifests[-1]["dataset"],
        "model": {
            "name": (
                "Alpha158-LGBM-WalkForward"
                if runtime.profile.family == "lightgbm"
                else "Alpha158-DNN-WalkForward"
            ),
            "fingerprint": external_id,
        },
        "runtime": runtime.to_manifest(),
        "canonicalConfig": manifests[-1].get("canonicalConfig"),
        "portfolioPolicySha256": manifests[-1].get("portfolioPolicySha256"),
        "lineage": aggregate_lineage,
        "promotion": {
            "status": "PROMOTED" if promoted else "CANDIDATE",
            "decision": "PROMOTE" if promoted else "RESEARCH_ONLY",
            "gateMode": "aggregate_oos_and_final_holdout",
            "aggregateOosGate": aggregate_gate,
            "finalHoldoutGate": final_manifest.get("promotion", {}),
            "componentValidationReports": [manifest.get("promotion", {}) for manifest in manifests[:-1]],
        },
        "timings": timings,
        "folds": [asdict(fold) for fold in folds],
        "execution": manifests[-1]["execution"],
        "metrics": metrics,
        "componentRuns": component_runs,
        "artifacts": [
            {"name": report_path.name, "localPath": str(report_path), "rows": len(combined)},
            {"name": audit_path.name, "localPath": str(audit_path), "rows": len(combined_audit)},
            {"name": holdings_path.name, "localPath": str(holdings_path), "rows": len(combined_holdings)},
            {"name": timings_path.name, "localPath": str(timings_path)},
            {"name": aggregate_gate_path.name, "localPath": str(aggregate_gate_path)},
        ],
    }
    if promoted:
        payload["latestTargets"] = manifests[-1]["latestTargets"]
    from .backtest_report import ReportArtifacts, write_backtest_report

    payload["artifacts"].extend(
        ReportArtifacts(
            markdown_path=output_dir / "backtest_report.md",
            pdf_path=output_dir / "backtest_report.pdf",
            assets_dir=output_dir / "report_assets",
        ).manifest_entries()
    )
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_backtest_report(settings, output_dir)
    return manifest_path
