from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .corporate_actions import CorporateActionStore
from .extract import Extractor
from .file_lock import FileLock
from .quality import assert_quality, validate_raw_day, validate_raw_store, write_report
from .settings import Settings
from .store import PartitionStore, frame_content_sha256
from .symbols import ts_to_qlib
from .universe import configured_universe, membership_fingerprint


def _atomic_json(payload: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(temporary, path)
    return path


@dataclass(frozen=True)
class DailySyncConfig:
    timezone_name: str = "Asia/Shanghai"
    ready_after: str = "17:30"
    market_lookback_trading_days: int = 5
    market_catchup_trading_days: int = 60
    corporate_action_lookback_calendar_days: int = 5

    @classmethod
    def from_settings(cls, settings: Settings) -> "DailySyncConfig":
        raw = settings.data.get("data_sync", {})
        data = raw if isinstance(raw, dict) else {}
        result = cls(
            timezone_name=str(data.get("timezone", cls.timezone_name)),
            ready_after=str(data.get("ready_after", cls.ready_after)),
            market_lookback_trading_days=int(
                data.get("market_lookback_trading_days", cls.market_lookback_trading_days)
            ),
            market_catchup_trading_days=int(
                data.get("market_catchup_trading_days", cls.market_catchup_trading_days)
            ),
            corporate_action_lookback_calendar_days=int(
                data.get(
                    "corporate_action_lookback_calendar_days",
                    cls.corporate_action_lookback_calendar_days,
                )
            ),
        )
        if result.market_lookback_trading_days < 1:
            raise ValueError("data_sync.market_lookback_trading_days must be positive")
        if result.market_catchup_trading_days < result.market_lookback_trading_days:
            raise ValueError(
                "data_sync.market_catchup_trading_days must be at least market_lookback_trading_days"
            )
        if result.corporate_action_lookback_calendar_days < 1:
            raise ValueError("data_sync.corporate_action_lookback_calendar_days must be positive")
        datetime.strptime(result.ready_after, "%H:%M")
        ZoneInfo(result.timezone_name)
        return result


class SingleInstanceLock(FileLock):
    """Backward-compatible non-blocking lock used by daily sync."""

    def __init__(self, path: Path):
        super().__init__(path, blocking=False, unavailable_message="daily sync is already running")


def _changed_symbols(old: pd.DataFrame, new: pd.DataFrame) -> set[str]:
    if "ts_code" not in old and "ts_code" not in new:
        return set()
    old_groups = (
        {
            str(code): frame_content_sha256(group, key_columns=("ts_code", "trade_date"))
            for code, group in old.groupby("ts_code", dropna=False)
        }
        if "ts_code" in old
        else {}
    )
    new_groups = (
        {
            str(code): frame_content_sha256(group, key_columns=("ts_code", "trade_date"))
            for code, group in new.groupby("ts_code", dropna=False)
        }
        if "ts_code" in new
        else {}
    )
    return {
        code for code in old_groups.keys() | new_groups.keys() if old_groups.get(code) != new_groups.get(code)
    }


class DailySyncService:
    def __init__(self, settings: Settings, extractor: Extractor | None = None):
        if not settings.uses_tushare_source():
            raise ValueError("daily-sync requires data_source.kind=tushare")
        self.settings = settings
        self.config = DailySyncConfig.from_settings(settings)
        self.extractor = extractor or Extractor(settings)
        self.store = PartitionStore(settings.paths.raw)
        self.actions = CorporateActionStore(settings)

    def _eligible_date(self, as_of: str | None) -> pd.Timestamp:
        if as_of:
            return pd.Timestamp(as_of).normalize()
        now = datetime.now(ZoneInfo(self.config.timezone_name))
        cutoff = time.fromisoformat(self.config.ready_after)
        day = pd.Timestamp(now.date())
        return day if now.time() >= cutoff else day - pd.Timedelta(days=1)

    def _market_date_plan(self, eligible: pd.Timestamp) -> tuple[list[str], list[str]]:
        all_dates = self.extractor.open_dates(
            str(self.settings.data["start_date"]),
            eligible.strftime("%Y%m%d"),
        )
        if not all_dates:
            raise ValueError(f"no open trading date on or before {eligible.date()}")
        lookback = all_dates[-self.config.market_lookback_trading_days :]
        catchup_window = all_dates[-self.config.market_catchup_trading_days :]
        missing = [date for date in catchup_window if not self.store.exists("daily", date)]
        selected = set(lookback) | set(missing)
        return all_dates, [date for date in all_dates if date in selected]

    def _market_dates(self, eligible: pd.Timestamp) -> list[str]:
        return self._market_date_plan(eligible)[1]

    def _fetch_market_frames(
        self, dates: list[str]
    ) -> tuple[dict[tuple[str, str], pd.DataFrame], dict[tuple[str, str], dict[str, Any]]]:
        staged: dict[tuple[str, str], pd.DataFrame] = {}
        metadata: dict[tuple[str, str], dict[str, Any]] = {}
        for trade_date in dates:
            validation: dict[str, pd.DataFrame] = {}
            for endpoint in self.extractor.endpoints:
                if not endpoint.enabled:
                    continue
                params: dict[str, str] = {"trade_date": trade_date}
                if endpoint.name == "suspend_d":
                    params["suspend_type"] = "S"
                result = self.extractor.client.fetch(
                    endpoint.name,
                    fields=endpoint.fields,
                    required=endpoint.required,
                    **params,
                )
                if not result.succeeded:
                    continue
                frame = result.data.copy()
                staged[(endpoint.name, trade_date)] = frame
                validation[endpoint.name] = frame
                metadata[(endpoint.name, trade_date)] = {
                    "api": endpoint.name,
                    "trade_date": trade_date,
                    "attempts": result.attempts,
                    "params": params,
                    "sync_mode": "daily_check",
                }
            for required in ("daily", "adj_factor", "daily_basic"):
                if required not in validation:
                    raise RuntimeError(
                        f"required endpoint did not produce a staged frame: {required} {trade_date}"
                    )
            report = validate_raw_day(validation, trade_date)
            assert_quality(report)
            write_report(report, self.settings.paths.quality / "raw" / f"{trade_date}.json")
        return staged, metadata

    def _factor_event_symbols(
        self,
        dates: list[str],
        staged: dict[tuple[str, str], pd.DataFrame],
    ) -> set[str]:
        if len(dates) < 2:
            return set()
        previous = staged.get(("adj_factor", dates[-2]), self.store.read("adj_factor", dates[-2]))
        current = staged.get(("adj_factor", dates[-1]), self.store.read("adj_factor", dates[-1]))
        if previous.empty or current.empty:
            return set()
        left = previous[["ts_code", "adj_factor"]].rename(columns={"adj_factor": "previous"})
        right = current[["ts_code", "adj_factor"]].rename(columns={"adj_factor": "current"})
        paired = left.merge(right, on="ts_code", how="inner")
        prior = pd.to_numeric(paired["previous"], errors="coerce")
        latest = pd.to_numeric(paired["current"], errors="coerce")
        changed = ~(np.isclose(prior, latest, rtol=1e-10, atol=1e-12) | (prior.isna() & latest.isna()))
        return set(paired.loc[changed, "ts_code"].astype(str).str.upper())

    def _reconcile_factor_histories(
        self,
        symbols: set[str],
        staged: dict[tuple[str, str], pd.DataFrame],
        metadata: dict[tuple[str, str], dict[str, Any]],
        end_date: str,
    ) -> None:
        for symbol in sorted(symbols):
            history = self.extractor.client.call(
                "adj_factor",
                fields="ts_code,trade_date,adj_factor",
                required=True,
                ts_code=symbol,
                start_date=str(self.settings.data["start_date"]),
                end_date=end_date,
            )
            if history.empty:
                raise RuntimeError(f"adj_factor history is empty for {symbol}")
            history["trade_date"] = history["trade_date"].astype(str)
            for trade_date, additions in history.groupby("trade_date", sort=True):
                key = ("adj_factor", str(trade_date))
                current = staged.get(key)
                if current is None:
                    current = self.store.read(*key)
                if current.empty:
                    retained = current
                elif "ts_code" not in current:
                    raise ValueError(f"stored adj_factor partition is missing ts_code: {trade_date}")
                else:
                    retained = current.loc[current["ts_code"].astype(str).str.upper() != symbol]
                merged = pd.concat(
                    [retained, additions],
                    ignore_index=True,
                )
                staged[key] = merged.sort_values("ts_code", kind="stable").reset_index(drop=True)
                metadata[key] = {
                    "api": "adj_factor",
                    "trade_date": str(trade_date),
                    "params": {"ts_code": symbol},
                    "sync_mode": "factor_history_reconcile",
                }

    def _promote_raw(
        self,
        staged: dict[tuple[str, str], pd.DataFrame],
        metadata: dict[tuple[str, str], dict[str, Any]],
        *,
        check_only: bool,
    ) -> tuple[list[str], set[str], list[dict[str, Any]]]:
        changed_dates: set[str] = set()
        revised_symbols: set[str] = set()
        changes: list[dict[str, Any]] = []
        for dataset, trade_date in sorted(staged):
            frame = staged[(dataset, trade_date)]
            old = self.store.read(dataset, trade_date)
            existed = self.store.exists(dataset, trade_date)
            logical_hash = frame_content_sha256(frame, key_columns=("ts_code", "trade_date"))
            old_hash = frame_content_sha256(old, key_columns=("ts_code", "trade_date")) if existed else None
            if old_hash == logical_hash:
                continue
            symbols = _changed_symbols(old, frame)
            changes.append(
                {
                    "dataset": dataset,
                    "trade_date": trade_date,
                    "old_content_sha256": old_hash,
                    "new_content_sha256": logical_hash,
                    "changed_symbol_count": len(symbols),
                }
            )
            changed_dates.add(trade_date)
            if existed:
                revised_symbols.update(symbols)
            if not check_only:
                self.store.write_if_changed(
                    dataset,
                    trade_date,
                    frame,
                    metadata[(dataset, trade_date)],
                )
        return sorted(changed_dates), revised_symbols, changes

    def _qlib_last_date(self) -> str | None:
        from .dataset_resolver import resolve_dataset

        path = resolve_dataset(self.settings).data_path / "calendars" / "day.txt"
        if not path.is_file():
            return None
        values = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return pd.Timestamp(values[-1]).strftime("%Y%m%d") if values else None

    def _refresh_metadata(self, dates: list[str]) -> dict[str, Any]:
        self.extractor.fetch_stock_master()
        benchmark = self.extractor.sync_benchmark("SH000300", dates[0], dates[-1])
        universe_rows = None
        configured = configured_universe(self.settings)
        if configured is not None:
            end = pd.Timestamp(dates[-1])
            snapshot_path = self.settings.paths.metadata / "universe_snapshots" / f"{configured[0]}.parquet"
            start = (
                (end - pd.DateOffset(months=2)).replace(day=1)
                if snapshot_path.is_file()
                else pd.Timestamp(self.settings.data["start_date"])
            )
            universe_rows = len(
                self.extractor.sync_universe_membership(
                    start.strftime("%Y%m%d"),
                    end.strftime("%Y%m%d"),
                )
            )
        return {
            "benchmark_rows": len(benchmark),
            "universe_intervals": universe_rows,
            "universe_membership_sha256": membership_fingerprint(self.settings),
        }

    def _publish_qlib(
        self,
        changed_dates: list[str],
        revised_symbols: set[str],
        *,
        force_full: bool,
        sync_context: dict[str, object],
    ) -> dict[str, Any]:
        from .normalize import (
            build_all_curated,
            build_curated_day,
            export_full_staging,
            export_incremental_staging,
            export_symbol_repair_staging,
        )
        from .qlib_export import dump_full, dump_update_and_fix
        from .lakehouse import freeze_pipeline_layers

        for trade_date in changed_dates:
            if self.store.exists("daily", trade_date):
                build_curated_day(self.settings, trade_date, force=True)
        last_date = self._qlib_last_date()
        release_store = self.settings.data.get("release_store", {})
        publish_release = (
            self.settings.mode == "standalone"
            and isinstance(release_store, dict)
            and bool(release_store.get("publish_on_sync", False))
        )
        if publish_release and (force_full or last_date is None or changed_dates or revised_symbols):
            from .dataset_registry import DatasetRegistry
            from .releases import publish_local_research_release

            build_all_curated(self.settings)
            export_full_staging(self.settings, force=True)
            snapshots = freeze_pipeline_layers(
                self.settings,
                mode="full_release",
                gold_sources=(("qlib_input", self.settings.paths.staging_full),),
            )
            registry = DatasetRegistry(self.settings.registry_path)
            parent_release_id = registry.resolve_release_alias("research-release-current")
            release = publish_local_research_release(
                self.settings,
                start=str(self.settings.data["start_date"]),
                end=(
                    max(changed_dates)
                    if changed_dates
                    else str(sync_context.get("eligible_date") or self.settings.data["end_date"])
                ),
                parent_release_id=parent_release_id,
            )
            sync_context.update(
                {
                    "data_release_id": release.data_release_id,
                    "data_release_manifest_sha256": release.manifest_sha256,
                    "dataset_parents": [
                        {"version_id": snapshots[-1]["version_id"], "relation": "converted_from"}
                    ],
                }
            )
            path = dump_full(self.settings, sync_context=sync_context, promote_alias=False)
            dataset_manifest = json.loads((path / "dataset_manifest.json").read_text(encoding="utf-8"))
            registry.register_release(release, governance_level="research")
            registry.promote_research_snapshot(
                release_alias="research-release-current",
                data_release_id=release.data_release_id,
                dataset_alias=self.settings.qlib_dataset_ref,
                dataset_version_id=str(dataset_manifest["version_id"]),
            )
            return {
                "mode": "full_release",
                "append_dates": changed_dates,
                "repair_symbols": sorted(revised_symbols),
                "data_release_id": release.data_release_id,
            }
        if force_full or last_date is None:
            build_all_curated(self.settings)
            export_full_staging(self.settings, force=True)
            snapshots = freeze_pipeline_layers(
                self.settings,
                mode="full",
                gold_sources=(("qlib_input", self.settings.paths.staging_full),),
            )
            sync_context["dataset_parents"] = [
                {"version_id": snapshots[-1]["version_id"], "relation": "converted_from"}
            ]
            dump_full(self.settings, sync_context=sync_context)
            return {"mode": "full", "append_dates": [], "repair_symbols": []}

        append_dates = [date for date in self.store.list_dates("daily") if date > last_date]
        repair_symbols = sorted(
            {ts_to_qlib(symbol) for symbol in revised_symbols if symbol and symbol.lower() != "nan"}
        )
        if append_dates:
            for trade_date in append_dates:
                build_curated_day(self.settings, trade_date, force=True)
            export_incremental_staging(self.settings, append_dates)
        if repair_symbols:
            export_symbol_repair_staging(self.settings, repair_symbols)
        mode = (
            "update_fix"
            if append_dates and repair_symbols
            else ("update" if append_dates else ("repair" if repair_symbols else "none"))
        )
        if append_dates or repair_symbols:
            sources = []
            if append_dates:
                sources.append(("qlib_update", self.settings.paths.staging_update))
            if repair_symbols:
                sources.append(("qlib_repair", self.settings.paths.staging_repair))
            snapshots = freeze_pipeline_layers(self.settings, mode=mode, gold_sources=sources)
            sync_context["dataset_parents"] = [
                {"version_id": snapshots[-1]["version_id"], "relation": "converted_from"}
            ]
            dump_update_and_fix(
                self.settings,
                append=bool(append_dates),
                repair=bool(repair_symbols),
                sync_context=sync_context,
            )
        return {"mode": mode, "append_dates": append_dates, "repair_symbols": repair_symbols}

    def run(
        self,
        *,
        as_of: str | None = None,
        check_only: bool = False,
        force_full: bool = False,
    ) -> Path:
        started = datetime.now(timezone.utc)
        run_id = f"{started:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
        run_dir = self.settings.paths.state / "daily_sync" / "runs" / run_id
        manifest_path = run_dir / "manifest.json"
        payload: dict[str, Any] = {
            "schema_version": "1.0",
            "run_id": run_id,
            "status": "running",
            "check_only": check_only,
            "started_at_utc": started.isoformat(),
        }
        _atomic_json(payload, manifest_path)
        from .dataset_registry import DatasetRegistry

        run_registry = DatasetRegistry(self.settings.registry_path)
        run_registry.start_pipeline_run(run_id, "daily_sync", manifest_path=manifest_path)
        try:
            eligible = self._eligible_date(as_of)
            expected_dates, dates = self._market_date_plan(eligible)
            staged, metadata = self._fetch_market_frames(dates)
            factor_events = self._factor_event_symbols(dates, staged)
            self._reconcile_factor_histories(factor_events, staged, metadata, dates[-1])
            dividend = self.actions.sync_incremental(
                self.extractor.client,
                as_of=eligible,
                lookback_calendar_days=self.config.corporate_action_lookback_calendar_days,
                full_symbols=factor_events,
                check_only=check_only,
            )
            changed_dates, revised_symbols, raw_changes = self._promote_raw(
                staged, metadata, check_only=check_only
            )
            metadata_result: dict[str, Any] | None = None
            qlib_result: dict[str, Any] | None = None
            raw_integrity: dict[str, object] | None = None
            if not check_only:
                pending_path = self.settings.paths.state / "daily_sync" / "pending_publish.json"
                if pending_path.is_file():
                    pending = json.loads(pending_path.read_text(encoding="utf-8"))
                    changed_dates = sorted(
                        set(changed_dates) | {str(value) for value in pending.get("changed_trade_dates", [])}
                    )
                    revised_symbols.update(str(value) for value in pending.get("revised_symbols", []))
                _atomic_json(
                    {
                        "schema_version": "1.0",
                        "status": "pending",
                        "run_id": run_id,
                        "changed_trade_dates": changed_dates,
                        "revised_symbols": sorted(revised_symbols),
                    },
                    pending_path,
                )
                integrity_report = validate_raw_store(
                    self.store,
                    expected_dates=expected_dates,
                    deep_dates=sorted(set(dates) | set(changed_dates)),
                )
                write_report(
                    integrity_report,
                    self.settings.paths.quality / "raw_dataset" / f"{run_id}.json",
                )
                raw_integrity = integrity_report.to_dict()
                assert_quality(integrity_report)
                metadata_result = self._refresh_metadata(dates)
                sync_context: dict[str, object] = {
                    "run_id": run_id,
                    "eligible_date": eligible.strftime("%Y-%m-%d"),
                    "changed_trade_dates": changed_dates,
                    "revised_symbols": sorted(revised_symbols),
                    "factor_event_symbols": sorted(factor_events),
                    "dividend_changed_symbols": dividend["changed_symbols"],
                }
                qlib_result = self._publish_qlib(
                    changed_dates,
                    revised_symbols,
                    force_full=force_full,
                    sync_context=sync_context,
                )
                _atomic_json(
                    {
                        "schema_version": "1.0",
                        "status": "clear",
                        "run_id": run_id,
                        "changed_trade_dates": [],
                        "revised_symbols": [],
                    },
                    pending_path,
                )
            published = bool(raw_changes or dividend["changed_symbol_count"])
            if qlib_result is not None:
                published = published or qlib_result.get("mode") != "none"
            payload.update(
                {
                    "status": "checked" if check_only else ("published" if published else "noop"),
                    "eligible_date": eligible.strftime("%Y-%m-%d"),
                    "checked_trade_dates": dates,
                    "factor_event_symbols": sorted(factor_events),
                    "raw_changes": raw_changes,
                    "raw_integrity": raw_integrity,
                    "changed_trade_dates": changed_dates,
                    "dividend": dividend,
                    "metadata": metadata_result,
                    "qlib": qlib_result,
                    "finished_at_utc": datetime.now(timezone.utc).isoformat(),
                }
            )
            _atomic_json(payload, manifest_path)
            _atomic_json(payload, self.settings.paths.state / "daily_sync" / "latest.json")
            dataset_version_id = None
            if qlib_result is not None and qlib_result.get("mode") != "none":
                from .dataset_resolver import resolve_dataset

                resolved_version_id = resolve_dataset(self.settings).version_id
                if run_registry.get_version(resolved_version_id) is not None:
                    dataset_version_id = resolved_version_id
            run_registry.finish_pipeline_run(
                run_id,
                status="SUCCEEDED",
                dataset_version_id=dataset_version_id,
                manifest_path=manifest_path,
            )
            return manifest_path
        except Exception as exc:
            payload.update(
                {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error_summary": "daily sync failed; inspect the local process output",
                    "finished_at_utc": datetime.now(timezone.utc).isoformat(),
                }
            )
            _atomic_json(payload, manifest_path)
            _atomic_json(payload, self.settings.paths.state / "daily_sync" / "latest.json")
            run_registry.finish_pipeline_run(
                run_id,
                status="FAILED",
                manifest_path=manifest_path,
                error_code=type(exc).__name__,
            )
            raise


def run_daily_sync(
    settings: Settings,
    *,
    as_of: str | None = None,
    check_only: bool = False,
    force_full: bool = False,
) -> Path:
    lock_path = settings.paths.state / "daily_sync" / "daily_sync.lock"
    with SingleInstanceLock(lock_path):
        return DailySyncService(settings).run(
            as_of=as_of,
            check_only=check_only,
            force_full=force_full,
        )
