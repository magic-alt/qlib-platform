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
    reports: list[pd.DataFrame] = []
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
        report_ref = next(item for item in manifest["artifacts"] if item["name"] == "portfolio_report.parquet")
        report = pd.read_parquet(report_ref["localPath"])
        if reports and pd.Timestamp(report.index.min()) <= pd.Timestamp(reports[-1].index.max()):
            raise ValueError(f"overlapping OOS reports at fold {fold.key}")
        reports.append(report)
    combined = pd.concat(reports).sort_index()
    fold_ids = [item["externalRunId"] for item in manifests]
    external_id = hashlib.sha256("|".join(fold_ids).encode()).hexdigest()[:32]
    output_dir = settings.paths.output / "research" / external_id
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "portfolio_report.parquet"
    combined.to_parquet(report_path)
    metrics: dict[str, float] = {}
    for column in ("return", "bench", "cost"):
        if column in combined:
            values = pd.to_numeric(combined[column], errors="coerce").dropna()
            metrics[f"{column}Total"] = float((1.0 + values).prod() - 1.0)
    latest = manifests[-1]["latestTargets"]
    payload = {
        "schemaVersion": "1.0",
        "externalRunId": external_id,
        "runKind": "walk_forward",
        "name": f"Qlib CSI300 walk-forward {start_date}..{end_date}",
        "dataset": manifests[-1]["dataset"],
        "model": {"name": "Alpha158-LGBM-WalkForward", "fingerprint": external_id},
        "folds": [asdict(fold) for fold in folds],
        "execution": manifests[-1]["execution"],
        "metrics": metrics,
        "artifacts": [{"name": report_path.name, "localPath": str(report_path), "rows": len(combined)}],
        "latestTargets": latest,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path
