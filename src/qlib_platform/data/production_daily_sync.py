from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Iterable

import pandas as pd

from qlib_platform.data.corporate_actions import CorporateActionStore
from qlib_platform.data.daily_sync import DailySyncConfig
from qlib_platform.data.ingestion import Extractor
from qlib_platform.data.planned_daily_sync import (
    PlannedDailySyncService,
    _normalize_factor_history,
)
from qlib_platform.data.store import PartitionStore
from qlib_platform.settings import Settings

_REQUIRED = ("daily", "adj_factor", "daily_basic")
_OPTIONAL_DEFAULTS = {
    "moneyflow": True,
    "stk_limit": True,
    "suspend_d": True,
    "stock_st": True,
}


class ProductionDailySyncService(PlannedDailySyncService):
    """Production fast path for #129.

    This class keeps the durable plan/apply contract from ``PlannedDailySyncService``
    while making the planner strictly local and bounded:

    * constructing/planning does not instantiate a provider client or require a token;
    * endpoint watermarks use manifests instead of reading historical Parquet payloads;
    * cached adj-factor symbol histories carry an ``indexed_through`` watermark and
      catch up all requested symbols in one pass over only the stale canonical dates.

    Apply remains fail closed and lazily creates the configured provider only when a
    remote fetch is actually required.
    """

    def __init__(self, settings: Settings, extractor: Any | None = None) -> None:
        if not settings.uses_tushare_source():
            raise ValueError("daily-sync requires data_source.kind=tushare")
        self.settings = settings
        self.config = DailySyncConfig.from_settings(settings)
        self._extractor: Any | None = extractor
        self.store = PartitionStore(settings.paths.raw)
        self.actions = CorporateActionStore(settings)
        self.plan_root = settings.paths.state / "daily_sync" / "plans"
        self.stage_base = settings.paths.root / "staging" / "daily_sync"
        self.factor_index_root = settings.paths.state / "daily_sync" / "factor_index"

    @property
    def extractor(self) -> Any:
        if self._extractor is None:
            self._extractor = Extractor(self.settings)
        return self._extractor

    @extractor.setter
    def extractor(self, value: Any) -> None:
        self._extractor = value

    def _enabled_endpoints(self) -> tuple[str, ...]:
        """Resolve daily endpoint names from config without creating a provider."""

        source = self.settings.data.get("data_source", {})
        source = source if isinstance(source, Mapping) else {}
        optional = source.get("optional_endpoints", {})
        if not isinstance(optional, Mapping):
            legacy = self.settings.data.get("tushare", {})
            legacy = legacy if isinstance(legacy, Mapping) else {}
            optional = legacy.get("optional_endpoints", {})
            optional = optional if isinstance(optional, Mapping) else {}
        result = list(_REQUIRED)
        for name, default in _OPTIONAL_DEFAULTS.items():
            if bool(optional.get(name, default)):
                result.append(name)
        return tuple(result)

    def _required_partition_current(self, dataset: str, trade_date: str) -> bool:
        """Use the partition manifest for planning; payload reads are a deep-gate job."""

        if not self.store.exists(dataset, trade_date):
            return False
        manifest = self.store.read_manifest(dataset, trade_date)
        status = str(manifest.get("status") or "")
        rows = manifest.get("rows")
        if status:
            if status != "success":
                return False
            if rows is not None:
                try:
                    return int(rows) > 0
                except (TypeError, ValueError):
                    return False
        # Compatibility fallback for pre-manifest/pre-row-count data.  This branch is
        # intentionally exceptional so modern routine planning remains metadata-only.
        try:
            return not self.store.read(dataset, trade_date).empty
        except Exception:
            return False

    def _watermarks(self, datasets: Iterable[str]) -> dict[str, dict[str, Any]]:
        """Find the latest successful partition without reading the whole history."""

        result: dict[str, dict[str, Any]] = {}
        for dataset in datasets:
            dates = self.store.list_dates(dataset)
            latest = next(
                (date for date in reversed(dates) if self._required_partition_current(dataset, date)),
                None,
            )
            result[dataset] = {
                "last_success": latest,
                "partition_count": len(dates),
            }
        return result

    def _factor_index_watermark(self, symbol: str, history: pd.DataFrame) -> str:
        manifest_path = self._factor_index_manifest_path(symbol)
        if manifest_path.is_file():
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                payload = {}
            value = str(payload.get("indexed_through") or "")
            if value:
                return value
        if history.empty or "trade_date" not in history:
            return ""
        return str(history["trade_date"].astype(str).max())

    def _load_factor_histories(
        self,
        symbols: set[str],
        *,
        staged_plan_id: str,
    ) -> tuple[dict[str, pd.DataFrame], int]:
        """Bring all symbol indexes current with one bounded canonical scan.

        If 14 symbols have never been indexed, 2,500 canonical partitions are read
        once. If they were indexed through yesterday, only today's canonical partition
        is read once. The operation count is therefore proportional to stale dates,
        never ``symbols * history_days``.
        """

        normalized_symbols = {str(value).upper() for value in symbols}
        if not normalized_symbols:
            return {}, 0

        histories: dict[str, pd.DataFrame] = {}
        watermarks: dict[str, str] = {}
        for symbol in normalized_symbols:
            path = self._factor_index_path(symbol)
            history = (
                _normalize_factor_history(pd.read_parquet(path))
                if path.is_file()
                else pd.DataFrame(columns=["ts_code", "trade_date", "adj_factor"])
            )
            histories[symbol] = history
            watermarks[symbol] = self._factor_index_watermark(symbol, history)

        canonical_dates = self.store.list_dates("adj_factor")
        latest_canonical = canonical_dates[-1] if canonical_dates else ""
        partition_reads = 0
        additions: dict[str, list[pd.DataFrame]] = {symbol: [] for symbol in normalized_symbols}

        for trade_date in canonical_dates:
            needed = {
                symbol
                for symbol, indexed_through in watermarks.items()
                if not indexed_through or trade_date > indexed_through
            }
            if not needed:
                continue
            frame = self.store.read("adj_factor", trade_date)
            partition_reads += 1
            if frame.empty or "ts_code" not in frame:
                continue
            codes = frame["ts_code"].astype(str).str.upper()
            selected = frame.loc[
                codes.isin(needed),
                ["ts_code", "trade_date", "adj_factor"],
            ].copy()
            if selected.empty:
                continue
            selected_codes = selected["ts_code"].astype(str).str.upper()
            for symbol in needed:
                rows = selected.loc[selected_codes.eq(symbol)]
                if not rows.empty:
                    additions[symbol].append(rows.copy())

        if latest_canonical:
            for symbol in normalized_symbols:
                if additions[symbol]:
                    histories[symbol] = _normalize_factor_history(
                        pd.concat([histories[symbol], *additions[symbol]], ignore_index=True)
                    )
                # Only canonical rows are persisted in the shared cache. Plan-stage
                # target rows are overlaid below and become cacheable only after Bronze
                # promotion succeeds.
                if watermarks[symbol] != latest_canonical or not self._factor_index_path(symbol).is_file():
                    self._write_factor_index(symbol, histories[symbol], latest_canonical)

        stage = self._market_stage(staged_plan_id)
        staged_dates = stage.list_dates("adj_factor")
        for symbol in normalized_symbols:
            staged_rows: list[pd.DataFrame] = []
            for trade_date in staged_dates:
                frame = stage.read("adj_factor", trade_date)
                if frame.empty or "ts_code" not in frame:
                    continue
                rows = frame.loc[
                    frame["ts_code"].astype(str).str.upper().eq(symbol),
                    ["ts_code", "trade_date", "adj_factor"],
                ]
                if not rows.empty:
                    staged_rows.append(rows.copy())
            if staged_rows:
                histories[symbol] = _normalize_factor_history(
                    pd.concat([histories[symbol], *staged_rows], ignore_index=True)
                )
        return histories, partition_reads
