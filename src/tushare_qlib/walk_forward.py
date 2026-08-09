from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from .settings import Settings
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
        turnover_source = frame["total_turnover"] if "total_turnover" in frame else pd.Series(0.0, index=frame.index)
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
) -> list[Fold]:
    dates = calendar[(calendar >= pd.Timestamp(start_date)) & (calendar <= pd.Timestamp(end_date))]
    if len(dates) < 252 * 6:
        raise ValueError("walk-forward requires at least six years of trading dates")
    holdout_start = _at_or_after(dates, dates[-1] - pd.DateOffset(months=holdout_months) + pd.Timedelta(days=1))
    test_start = _at_or_after(dates, dates[0] + pd.DateOffset(years=train_years, months=valid_months))
    folds: list[Fold] = []
    index = 0
    while test_start < holdout_start:
        test_end = _at_or_before(dates, min(test_start + pd.DateOffset(months=test_months) - pd.Timedelta(days=1), holdout_start - pd.Timedelta(days=1)))
        valid_end_pos = dates.get_loc(test_start) - embargo_days - 1
        valid_end = dates[valid_end_pos]
        valid_start = _at_or_after(dates, valid_end - pd.DateOffset(months=valid_months) + pd.Timedelta(days=1))
        train_end = dates[dates.get_loc(valid_start) - purge_days - 1]
        train_start = _at_or_after(dates, train_end - pd.DateOffset(years=train_years) + pd.Timedelta(days=1))
        folds.append(Fold(f"rolling_{index:02d}", (str(train_start.date()), str(train_end.date())), (str(valid_start.date()), str(valid_end.date())), (str(test_start.date()), str(test_end.date()))))
        index += 1
        test_start = _at_or_after(dates, test_end + pd.Timedelta(days=1))
    valid_end = dates[dates.get_loc(holdout_start) - embargo_days - 1]
    valid_start = _at_or_after(dates, valid_end - pd.DateOffset(months=valid_months) + pd.Timedelta(days=1))
    train_end = dates[dates.get_loc(valid_start) - purge_days - 1]
    train_start = _at_or_after(dates, train_end - pd.DateOffset(years=train_years) + pd.Timedelta(days=1))
    folds.append(Fold("final_holdout", (str(train_start.date()), str(train_end.date())), (str(valid_start.date()), str(valid_end.date())), (str(holdout_start.date()), str(dates[-2].date())), True))
    return folds


def run_walk_forward(
    settings: Settings,
    *,
    start_date: str,
    end_date: str,
    benchmark: str = "SH000300",
    topn: int = 30,
) -> Path:
    calendar_frame = pd.read_parquet(settings.paths.metadata / "trade_calendar.parquet")
    calendar = pd.DatetimeIndex(
        pd.to_datetime(calendar_frame.loc[pd.to_numeric(calendar_frame["is_open"], errors="coerce") == 1, "cal_date"])
        .dropna().sort_values().unique()
    )
    folds = build_walk_forward_plan(calendar, start_date, end_date)
    run_root = settings.paths.output / "research" / "walk_forward"
    run_root.mkdir(parents=True, exist_ok=True)
    manifests: list[dict[str, Any]] = []
    reports: list[tuple[str, pd.DataFrame]] = []
    audits: list[pd.DataFrame] = []
    holdings: list[pd.DataFrame] = []
    component_runs: list[dict[str, str]] = []
    for fold in folds:
        checkpoint = run_root / f"{fold.key}.json"
        if checkpoint.exists():
            fold_manifest_path = Path(json.loads(checkpoint.read_text(encoding="utf-8"))["manifest"])
        else:
            selection = train_backtest_select(
                settings,
                train=fold.train,
                valid=fold.valid,
                test=fold.test,
                benchmark=benchmark,
                topn=topn,
                experiment_name=f"lean_csi300_walk_forward_{fold.key}",
                run_kind="final_holdout" if fold.final_holdout else "walk_forward_fold",
            )
            model_id = str(pd.read_csv(selection)["model_id"].iloc[0])
            fold_manifest_path = settings.paths.output / "research" / model_id / "manifest.json"
            checkpoint.write_text(json.dumps({"manifest": str(fold_manifest_path)}, indent=2), encoding="utf-8")
        manifest = json.loads(fold_manifest_path.read_text(encoding="utf-8"))
        manifests.append(manifest)
        report = pd.read_parquet(_artifact_path(manifest, "portfolio_report.parquet"))
        if reports and pd.Timestamp(report.index.min()) <= pd.Timestamp(reports[-1][1].index.max()):
            raise ValueError(f"overlapping OOS reports at fold {fold.key}")
        reports.append((fold.key, report))
        audit = pd.read_parquet(_artifact_path(manifest, "strategy_audit.parquet"))
        audits.append(audit.assign(fold_key=fold.key))
        holding = pd.read_parquet(_artifact_path(manifest, "holdings.parquet"))
        holdings.append(holding.assign(fold_key=fold.key))
        component_runs.append(
            {
                "key": fold.key,
                "externalRunId": str(manifest["externalRunId"]),
                "manifestPath": str(fold_manifest_path),
            }
        )
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
    combined.to_parquet(report_path)
    combined_audit.to_parquet(audit_path, index=False)
    combined_holdings.to_parquet(holdings_path, index=False)
    metrics: dict[str, float] = {}
    for column in ("return", "bench", "cost"):
        if column in combined:
            values = pd.to_numeric(combined[column], errors="coerce").dropna()
            metrics[f"{column}Total"] = float((1.0 + values).prod() - 1.0)
    latest = manifests[-1]["latestTargets"]
    payload = {
        "schemaVersion": "1.1",
        "externalRunId": external_id,
        "runKind": "walk_forward",
        "name": f"Qlib CSI300 walk-forward {start_date}..{end_date}",
        "dataset": manifests[-1]["dataset"],
        "model": {"name": "Alpha158-LGBM-WalkForward", "fingerprint": external_id},
        "folds": [asdict(fold) for fold in folds],
        "execution": manifests[-1]["execution"],
        "metrics": metrics,
        "componentRuns": component_runs,
        "artifacts": [
            {"name": report_path.name, "localPath": str(report_path), "rows": len(combined)},
            {"name": audit_path.name, "localPath": str(audit_path), "rows": len(combined_audit)},
            {"name": holdings_path.name, "localPath": str(holdings_path), "rows": len(combined_holdings)},
        ],
        "latestTargets": latest,
    }
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
