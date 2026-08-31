from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

from .store import PartitionStore, frame_content_sha256, sha256_file


_REQUIRED_RAW_COLUMNS = {
    "daily": {"ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"},
    "adj_factor": {"ts_code", "trade_date", "adj_factor"},
    "daily_basic": {"ts_code", "trade_date"},
}


@dataclass(frozen=True)
class QualityResult:
    name: str
    passed: bool
    detail: str
    severity: str = "error"


@dataclass(frozen=True)
class QualityReport:
    scope: str
    results: tuple[QualityResult, ...]
    generated_at_utc: str

    @property
    def passed(self) -> bool:
        return not any((not r.passed) and r.severity == "error" for r in self.results)

    def to_dict(self) -> dict[str, object]:
        return {
            "scope": self.scope,
            "passed": self.passed,
            "generated_at_utc": self.generated_at_utc,
            "results": [asdict(r) for r in self.results],
        }


def make_report(scope: str, results: Iterable[QualityResult]) -> QualityReport:
    return QualityReport(scope, tuple(results), datetime.now(timezone.utc).isoformat())


def write_report(report: QualityReport, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def _column_check(df: pd.DataFrame, required: set[str]) -> QualityResult:
    missing = sorted(required - set(map(str, df.columns)))
    return QualityResult("required_columns", not missing, f"missing={missing}")


def validate_raw_day(
    frames: Mapping[str, pd.DataFrame],
    trade_date: str,
    *,
    min_adj_coverage: float = 0.995,
    min_basic_coverage: float = 0.98,
) -> QualityReport:
    results: list[QualityResult] = []
    for name, columns in _REQUIRED_RAW_COLUMNS.items():
        df = frames.get(name, pd.DataFrame())
        results.append(QualityResult(f"{name}_non_empty", not df.empty, f"rows={len(df)}"))
        check = _column_check(df, columns)
        results.append(QualityResult(f"{name}_{check.name}", check.passed, check.detail))
        if not df.empty and {"ts_code", "trade_date"}.issubset(df.columns):
            duplicates = int(df.duplicated(["ts_code", "trade_date"]).sum())
            results.append(QualityResult(f"{name}_unique_key", duplicates == 0, f"duplicates={duplicates}"))
            dates = set(df["trade_date"].astype(str).dropna().unique())
            results.append(
                QualityResult(
                    f"{name}_date_consistency",
                    dates == {trade_date},
                    f"dates={sorted(dates)[:5]}",
                )
            )

    daily = frames.get("daily", pd.DataFrame())
    if not daily.empty and "ts_code" in daily:
        daily_codes = set(daily["ts_code"].astype(str))
        for name, threshold in (("adj_factor", min_adj_coverage), ("daily_basic", min_basic_coverage)):
            other = frames.get(name, pd.DataFrame())
            other_codes = set(other["ts_code"].astype(str)) if "ts_code" in other else set()
            coverage = len(daily_codes & other_codes) / max(1, len(daily_codes))
            results.append(
                QualityResult(
                    f"{name}_coverage_vs_daily",
                    coverage >= threshold,
                    f"coverage={coverage:.6f}, threshold={threshold:.6f}",
                )
            )

        numeric = daily[[c for c in ["open", "high", "low", "close", "vol", "amount"] if c in daily]].apply(
            pd.to_numeric, errors="coerce"
        )
        bad_price = int(
            (numeric[[c for c in ["open", "high", "low", "close"] if c in numeric]] <= 0).any(axis=1).sum()
        )
        results.append(QualityResult("daily_positive_prices", bad_price == 0, f"bad_rows={bad_price}"))
        negative_volume = int((numeric[[c for c in ["vol", "amount"] if c in numeric]] < 0).any(axis=1).sum())
        results.append(
            QualityResult(
                "daily_nonnegative_volume_amount", negative_volume == 0, f"bad_rows={negative_volume}"
            )
        )

    return make_report(f"raw_day:{trade_date}", results)


def validate_raw_store(
    store: PartitionStore,
    *,
    expected_dates: Iterable[str],
    deep_dates: Iterable[str] = (),
) -> QualityReport:
    """Fail-closed verification for the canonical TuShare ``current`` view.

    Coverage is checked across the complete expected trading calendar. Payload,
    manifest, checksum, schema, key, and date checks are limited to ``deep_dates``
    so routine daily sync does not re-read the entire raw lake on every run.
    """

    expected = {str(value) for value in expected_dates}
    results: list[QualityResult] = []
    for dataset in _REQUIRED_RAW_COLUMNS:
        available = {
            trade_date
            for trade_date in store.list_dates(dataset)
            if store.data_path(dataset, trade_date).is_file()
        }
        missing = sorted(expected - available)
        results.append(
            QualityResult(
                f"{dataset}_calendar_coverage",
                not missing,
                f"expected={len(expected)}, available={len(available & expected)}, missing={missing[:20]}",
            )
        )

    for trade_date in sorted({str(value) for value in deep_dates}):
        frames: dict[str, pd.DataFrame] = {}
        for dataset in _REQUIRED_RAW_COLUMNS:
            data_path = store.data_path(dataset, trade_date)
            manifest_path = store.manifest_path(dataset, trade_date)
            prefix = f"{dataset}_{trade_date}"
            pair_exists = data_path.is_file() and manifest_path.is_file()
            results.append(
                QualityResult(
                    f"{prefix}_data_manifest_pair",
                    pair_exists,
                    f"data={data_path.is_file()}, manifest={manifest_path.is_file()}",
                )
            )
            if not pair_exists:
                continue
            try:
                manifest = store.read_manifest(dataset, trade_date)
                frame = store.read(dataset, trade_date)
            except Exception as exc:
                results.append(
                    QualityResult(
                        f"{prefix}_readable",
                        False,
                        f"error_type={type(exc).__name__}",
                    )
                )
                continue
            frames[dataset] = frame
            results.extend(
                (
                    QualityResult(
                        f"{prefix}_manifest_identity",
                        manifest.get("dataset") == dataset and str(manifest.get("trade_date")) == trade_date,
                        f"dataset={manifest.get('dataset')}, trade_date={manifest.get('trade_date')}",
                    ),
                    QualityResult(
                        f"{prefix}_file_size",
                        manifest.get("bytes") == data_path.stat().st_size,
                        f"manifest={manifest.get('bytes')}, actual={data_path.stat().st_size}",
                    ),
                    QualityResult(
                        f"{prefix}_file_sha256",
                        manifest.get("sha256") == sha256_file(data_path),
                        "manifest checksum matches payload" if manifest.get("sha256") else "missing sha256",
                    ),
                    QualityResult(
                        f"{prefix}_row_count",
                        manifest.get("rows") == len(frame),
                        f"manifest={manifest.get('rows')}, actual={len(frame)}",
                    ),
                    QualityResult(
                        f"{prefix}_columns",
                        manifest.get("columns") == [str(column) for column in frame.columns],
                        f"manifest={manifest.get('columns')}, actual={list(map(str, frame.columns))}",
                    ),
                )
            )
            if manifest.get("content_hash_kind") == "logical_frame_v1":
                logical_hash = frame_content_sha256(
                    frame,
                    key_columns=("ts_code", "trade_date"),
                )
                results.append(
                    QualityResult(
                        f"{prefix}_content_sha256",
                        manifest.get("content_sha256") == logical_hash,
                        "logical content checksum matches payload",
                    )
                )

        day_report = validate_raw_day(frames, trade_date)
        results.extend(
            QualityResult(
                f"{trade_date}_{result.name}",
                result.passed,
                result.detail,
                result.severity,
            )
            for result in day_report.results
        )

    return make_report("raw_store:current", results)


def validate_curated(
    df: pd.DataFrame, *, expected_trade_date: str | None = None, min_traded_coverage: float = 0.50
) -> QualityReport:
    results: list[QualityResult] = []
    required = {
        "symbol",
        "date",
        "ts_code",
        "close",
        "open",
        "high",
        "low",
        "adj_factor",
        "paused",
        "list_date",
    }
    results.append(_column_check(df, required))
    if not required.issubset(df.columns):
        return make_report("curated", results)

    duplicated = int(df.duplicated(["symbol", "date"]).sum())
    results.append(QualityResult("unique_symbol_date", duplicated == 0, f"duplicates={duplicated}"))
    results.append(QualityResult("non_empty", not df.empty, f"rows={len(df)}"))

    if expected_trade_date is not None and not df.empty:
        expected = pd.Timestamp(expected_trade_date).normalize()
        dates = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
        mismatch = int((dates != expected).sum())
        results.append(QualityResult("trade_date_consistency", mismatch == 0, f"mismatched_rows={mismatch}"))

    traded = df["close"].notna()
    traded_count = int(traded.sum())
    coverage = traded_count / max(1, len(df))
    results.append(
        QualityResult(
            "traded_coverage",
            coverage >= min_traded_coverage,
            f"traded={traded_count}, active={len(df)}, coverage={coverage:.4f}, threshold={min_traded_coverage:.4f}",
        )
    )

    prices = df.loc[traded, ["open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce")
    bad_price = int((prices.le(0).any(axis=1) | prices.isna().any(axis=1)).sum())
    results.append(QualityResult("positive_prices", bad_price == 0, f"bad_rows={bad_price}"))

    bad_ohlc = int(
        (
            (prices["high"] < prices[["open", "close", "low"]].max(axis=1))
            | (prices["low"] > prices[["open", "close", "high"]].min(axis=1))
        ).sum()
    )
    results.append(QualityResult("ohlc_relation", bad_ohlc == 0, f"bad_rows={bad_ohlc}"))

    factors = pd.to_numeric(df.loc[traded, "adj_factor"], errors="coerce")
    bad_factor = int((factors.isna() | (factors <= 0) | ~np.isfinite(factors)).sum())
    results.append(QualityResult("positive_adj_factor", bad_factor == 0, f"bad_rows={bad_factor}"))

    paused = pd.to_numeric(df["paused"], errors="coerce")
    paused_mismatch = int(((paused >= 0.5) & df["close"].notna()).sum())
    # Allow up to 5 mismatches — rare Tushare edge cases where a suspended
    # stock still carries a close price (e.g. morning halt then afternoon trade).
    results.append(
        QualityResult("paused_close_consistency", paused_mismatch <= 5, f"bad_rows={paused_mismatch}")
    )

    valid_symbols = df["symbol"].astype(str).str.fullmatch(r"(SH|SZ|BJ)\d{6}").fillna(False)
    invalid_symbols = int((~valid_symbols).sum())
    results.append(QualityResult("symbol_format", invalid_symbols == 0, f"bad_rows={invalid_symbols}"))

    return make_report("curated", results)


def validate_normalized(df: pd.DataFrame, symbol: str | None = None) -> QualityReport:
    results: list[QualityResult] = []
    required = {"date", "symbol", "open", "high", "low", "close", "volume", "money", "factor", "paused"}
    results.append(_column_check(df, required))
    if not required.issubset(df.columns):
        return make_report(f"normalized:{symbol or 'unknown'}", results)

    duplicates = int(df.duplicated(["symbol", "date"]).sum())
    results.append(QualityResult("unique_symbol_date", duplicates == 0, f"duplicates={duplicates}"))
    traded = df["close"].notna()
    factor = pd.to_numeric(df.loc[traded, "factor"], errors="coerce")
    bad_factor = int((factor.isna() | (factor <= 0) | ~np.isfinite(factor)).sum())
    results.append(QualityResult("positive_factor", bad_factor == 0, f"bad_rows={bad_factor}"))
    volume = pd.to_numeric(df.loc[traded, "volume"], errors="coerce")
    bad_volume = int((volume.isna() | (volume < 0) | ~np.isfinite(volume)).sum())
    results.append(QualityResult("nonnegative_volume", bad_volume == 0, f"bad_rows={bad_volume}"))
    return make_report(f"normalized:{symbol or 'unknown'}", results)


def assert_quality(report_or_results: QualityReport | Iterable[QualityResult]) -> None:
    if isinstance(report_or_results, QualityReport):
        failures = [r for r in report_or_results.results if not r.passed and r.severity == "error"]
    else:
        failures = [r for r in report_or_results if not r.passed and r.severity == "error"]
    if failures:
        raise AssertionError("; ".join(f"{r.name}: {r.detail}" for r in failures))
