from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from ..data_release import MARKET_IMPORT_PROFILE, DataRelease
from ..dataset_manifest import write_dataset_manifest
from ..dataset_registry import DatasetRegistry, DatasetVersion
from ..settings import Settings
from .publisher import ComponentSource, LocalReleasePublisher, release_store_root


MARKET_FIELDS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "money",
    "vwap",
    "factor",
    "paused",
    "is_limit_up",
    "is_limit_down",
)


def local_market_components(settings: Settings) -> list[ComponentSource]:
    return [
        ComponentSource("bars", settings.paths.raw / "daily"),
        ComponentSource("adjustment_factors", settings.paths.raw / "adj_factor"),
        ComponentSource("security_master", settings.paths.metadata / "stock_master.parquet"),
        ComponentSource("trading_calendar", settings.paths.metadata / "trade_calendar.parquet"),
    ]


def _component_files(source: ComponentSource) -> list[Path]:
    if source.source.is_file() and source.source.suffix.lower() == ".parquet":
        return [source.source]
    if source.source.is_dir():
        return sorted(source.source.rglob("*.parquet"))
    return []


def missing_market_components(settings: Settings) -> list[str]:
    return [item.role for item in local_market_components(settings) if not _component_files(item)]


def _read_parquet_files(paths: Iterable[Path], name: str) -> pd.DataFrame:
    files = list(paths)
    if not files:
        raise FileNotFoundError(f"{name} contains no parquet files")
    return pd.concat((pd.read_parquet(path) for path in files), ignore_index=True)


def _column(frame: pd.DataFrame, candidates: tuple[str, ...], name: str) -> str:
    for candidate in candidates:
        if candidate in frame:
            return candidate
    raise ValueError(f"{name} is missing; expected one of {list(candidates)}")


def _instrument(value: object) -> str:
    raw = str(value).strip().upper()
    suffix = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ)", raw)
    if suffix:
        return f"{suffix.group(2)}{suffix.group(1)}"
    prefix = re.fullmatch(r"(SH|SZ|BJ)(\d{6})", raw)
    if prefix:
        return raw
    if re.fullmatch(r"\d{6}", raw):
        exchange = "SH" if raw.startswith(("5", "6", "9")) else "BJ" if raw.startswith(("4", "8")) else "SZ"
        return f"{exchange}{raw}"
    raise ValueError(f"unsupported A-share instrument: {value!r}")


def _normalize_dates(values: pd.Series, name: str) -> pd.Series:
    parsed = pd.to_datetime(values.astype(str), errors="coerce").dt.normalize()
    if parsed.isna().any():
        raise ValueError(f"{name} contains invalid dates")
    return parsed


def _market_frame(release: DataRelease, start: str, end: str) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    bars = _read_parquet_files(release.files("bars"), "bars")
    date_column = _column(bars, ("trade_date", "date", "datetime"), "bars date")
    symbol_column = _column(bars, ("ts_code", "symbol", "instrument", "code"), "bars instrument")
    rename = {date_column: "date", symbol_column: "instrument"}
    amount_column = next((name for name in ("money", "amount", "turnover") if name in bars), None)
    if amount_column and amount_column != "money":
        rename[amount_column] = "money"
    bars = bars.rename(columns=rename)
    required = {"open", "high", "low", "close", "volume"}
    if missing := required - set(bars):
        raise ValueError(f"bars are missing OHLCV fields: {sorted(missing)}")
    bars["date"] = _normalize_dates(bars["date"], "bars")
    bars["instrument"] = bars["instrument"].map(_instrument)
    begin = pd.Timestamp(start).normalize()
    finish = pd.Timestamp(end).normalize()
    bars = bars.loc[bars["date"].between(begin, finish)].copy()
    if bars.empty:
        raise ValueError("bars do not cover the requested bootstrap window")
    if bars.duplicated(["date", "instrument"]).any():
        raise ValueError("bars contain duplicate date/instrument rows")

    factors = _read_parquet_files(release.files("adjustment_factors"), "adjustment_factors")
    factor_date = _column(factors, ("trade_date", "date", "datetime"), "adjustment_factors date")
    factor_symbol = _column(
        factors,
        ("ts_code", "symbol", "instrument", "code"),
        "adjustment_factors instrument",
    )
    factor_value = _column(factors, ("adj_factor", "factor"), "adjustment factor")
    factors = factors.rename(
        columns={factor_date: "date", factor_symbol: "instrument", factor_value: "factor"}
    )
    factors["date"] = _normalize_dates(factors["date"], "adjustment_factors")
    factors["instrument"] = factors["instrument"].map(_instrument)
    factors = factors[["date", "instrument", "factor"]]
    if factors.duplicated(["date", "instrument"]).any():
        raise ValueError("adjustment_factors contain duplicate date/instrument rows")
    bars = bars.merge(factors, on=["date", "instrument"], how="left", validate="one_to_one")
    if bars["factor"].isna().any():
        raise ValueError("adjustment_factors do not cover every OHLCV row")

    calendar = _read_parquet_files(release.files("trading_calendar"), "trading_calendar")
    calendar_date = _column(calendar, ("cal_date", "trade_date", "date", "datetime"), "trading_calendar date")
    if "is_open" in calendar:
        calendar = calendar.loc[pd.to_numeric(calendar["is_open"], errors="coerce").eq(1)]
    dates = pd.DatetimeIndex(_normalize_dates(calendar[calendar_date], "trading_calendar").unique())
    dates = dates[(dates >= begin) & (dates <= finish)].sort_values()
    if dates.empty:
        raise ValueError("trading_calendar has no open dates in the requested window")
    missing_dates = pd.DatetimeIndex(bars["date"].unique()).difference(dates)
    if len(missing_dates):
        raise ValueError(f"bars contain dates absent from trading_calendar: {missing_dates[:5].tolist()}")

    for column in ("open", "high", "low", "close", "volume", "factor"):
        bars[column] = pd.to_numeric(bars[column], errors="coerce")
    if bars[list(required | {"factor"})].isna().any().any():
        raise ValueError("OHLCV or adjustment factor values contain non-numeric data")
    if "money" in bars:
        bars["money"] = pd.to_numeric(bars["money"], errors="coerce")
    else:
        bars["money"] = bars["close"] * bars["volume"]
    bars["money"] = bars["money"].fillna(bars["close"] * bars["volume"])
    bars["vwap"] = np.where(bars["volume"].gt(0), bars["money"] / bars["volume"], bars["close"])
    bars["paused"] = 0.0
    bars["is_limit_up"] = 0.0
    bars["is_limit_down"] = 0.0
    return bars.sort_values(["instrument", "date"], kind="stable"), dates


def _write_provider(root: Path, frame: pd.DataFrame, dates: pd.DatetimeIndex) -> None:
    calendars = root / "calendars"
    instruments = root / "instruments"
    calendars.mkdir(parents=True)
    instruments.mkdir()
    calendars.joinpath("day.txt").write_text("\n".join(dates.strftime("%Y-%m-%d")) + "\n", encoding="utf-8")
    offsets = {date: position for position, date in enumerate(dates)}
    instrument_lines: list[str] = []
    for instrument, block in frame.groupby("instrument", sort=True):
        block = block.set_index("date").sort_index()
        first = pd.Timestamp(block.index.min()).normalize()
        last = pd.Timestamp(block.index.max()).normalize()
        active_dates = dates[(dates >= first) & (dates <= last)]
        instrument_lines.append(f"{str(instrument).lower()}\t{first.date()}\t{last.date()}\n")
        feature_root = root / "features" / str(instrument).lower()
        feature_root.mkdir(parents=True)
        for field in MARKET_FIELDS:
            values = pd.to_numeric(block[field].reindex(active_dates), errors="coerce").to_numpy(dtype="<f4")
            payload = np.concatenate((np.asarray([offsets[first]], dtype="<f4"), values))
            payload.tofile(feature_root / f"{field}.day.bin")
    instruments.joinpath("all.txt").write_text("".join(instrument_lines), encoding="utf-8")


def publish_local_market_release(
    settings: Settings,
    *,
    start: str,
    end: str,
) -> tuple[DataRelease, DatasetVersion]:
    missing = missing_market_components(settings)
    if missing:
        raise ValueError(
            "ashare_market_import_v1 is missing required components: "
            f"{missing}; required=['bars', 'adjustment_factors', 'security_master', 'trading_calendar']"
        )
    coverage = {
        "start": str(pd.Timestamp(start).date()),
        "end": str(pd.Timestamp(end).date()),
    }
    release = LocalReleasePublisher(release_store_root(settings)).publish(
        profile=MARKET_IMPORT_PROFILE,
        components=local_market_components(settings),
        coverage=coverage,
        policies={
            "governanceLevel": "exploratory",
            "certifiedPromotionAllowed": False,
            "phase2Phase3Allowed": False,
            "artifactV2Allowed": False,
            "targetPortfolioAllowed": False,
            "researchPromotionAllowed": False,
        },
        lineage={
            "producer": "qlib-platform",
            "sourceType": "local_market_parquet",
            "priceAdjustment": "source_factor_preserved",
        },
    )
    versions = settings.qlib_versions_root
    versions.mkdir(parents=True, exist_ok=True)
    candidate = Path(tempfile.mkdtemp(prefix=".market-import.", dir=versions))
    try:
        frame, calendar = _market_frame(release, coverage["start"], coverage["end"])
        _write_provider(candidate, frame, calendar)
        manifest_path, payload = write_dataset_manifest(
            candidate,
            dataset_name=settings.qlib_dataset_name,
            layer="qlib",
            semantic_contract={
                "data_release_id": release.data_release_id,
                "data_release_manifest_sha256": release.manifest_sha256,
                "governance_level": "exploratory",
                "source_type": "local_market_parquet",
                "fields": list(MARKET_FIELDS),
            },
            coverage=coverage,
            extra={
                "dataset_id": release.data_release_id,
                "data_release_id": release.data_release_id,
                "data_release_manifest_sha256": release.manifest_sha256,
                "fields": list(MARKET_FIELDS),
                "mode": "market_import",
            },
        )
        version_id = str(payload["version_id"])
        final = versions / version_id
        payload["data_path"] = str(final.resolve())
        payload["status"] = "VALIDATED"
        manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if final.exists():
            shutil.rmtree(candidate)
        else:
            os.replace(candidate, final)
        final_manifest = final / "dataset_manifest.json"
        registry = DatasetRegistry(settings.registry_path)
        registry.register_release(release, governance_level="exploratory")
        version = registry.register_dataset(
            json.loads(final_manifest.read_text(encoding="utf-8")), final_manifest
        )
        registry.promote_research_snapshot(
            release_alias="research-release-current",
            data_release_id=release.data_release_id,
            dataset_alias=settings.qlib_dataset_ref,
            dataset_version_id=version.version_id,
        )
        resolved = registry.get_version(version.version_id)
        assert resolved is not None
        return release, resolved
    except Exception:
        shutil.rmtree(candidate, ignore_errors=True)
        raise
