"""Concurrent execution strategy for the wide Tushare data catalogue."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Iterable

import pandas as pd
from loguru import logger

from .client import FetchResult
from .extended_data import EXTENDED_ENDPOINTS, EXTENDED_GROUPS, ExtendedDataBackfill, ExtendedEndpoint
from .store import frame_content_sha256


def _changed_symbol_set(old: pd.DataFrame, new: pd.DataFrame) -> set[str]:
    """Return symbols whose logical rows differ between two endpoint snapshots."""

    if "ts_code" not in old and "ts_code" not in new:
        return set()

    def _fingerprints(frame: pd.DataFrame) -> dict[str, str]:
        if "ts_code" not in frame:
            return {}
        return {
            str(code).upper(): frame_content_sha256(
                group,
                key_columns=("ts_code", "end_date", "ann_date", "trade_date"),
            )
            for code, group in frame.groupby("ts_code", dropna=False)
        }

    old_groups = _fingerprints(old)
    new_groups = _fingerprints(new)
    return {
        code for code in old_groups.keys() | new_groups.keys() if old_groups.get(code) != new_groups.get(code)
    }


class FastExtendedDataBackfill(ExtendedDataBackfill):
    """Use all-market batches first and concurrent calls only where unavoidable.

    Symbol-history APIs do not expose an all-market range query. Those calls
    are independently partitioned and can safely overlap; the shared client
    limiter remains the authority for the account-wide request rate.
    """

    def __init__(self, *args: Any, max_workers: int | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        source = self.settings.data.get("tushare", {})
        configured = source.get("extended_max_workers", 8) if isinstance(source, dict) else 8
        self.max_workers = int(max_workers if max_workers is not None else configured)
        if self.max_workers < 1:
            raise ValueError("max_workers must be positive")

    def _fetch_one(
        self,
        endpoint: ExtendedEndpoint,
        partition: str,
        params: dict[str, str],
        *,
        check_only: bool,
    ) -> tuple[str, bool, set[str]]:
        result: FetchResult = self.client.fetch(endpoint.name, required=False, **params)
        metadata = {
            "api": endpoint.name,
            "group": endpoint.group,
            "partition": partition,
            "params": params,
            "attempts": result.attempts,
            "error": result.error,
            "requested_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        changed = False
        changed_symbols: set[str] = set()
        if result.succeeded:
            old = self.store.read(endpoint.name, partition)
            if endpoint.group == "financial":
                changed_symbols = _changed_symbol_set(old, result.data)
            old_hash = (
                frame_content_sha256(
                    old,
                    key_columns=("ts_code", "end_date", "ann_date", "trade_date"),
                )
                if self.store.exists(endpoint.name, partition)
                else None
            )
            new_hash = frame_content_sha256(
                result.data,
                key_columns=("ts_code", "end_date", "ann_date", "trade_date"),
            )
            changed = old_hash != new_hash
            if not check_only:
                _, changed, _ = self.store.write_if_changed(
                    endpoint.name,
                    partition,
                    result.data,
                    metadata,
                    key_columns=("ts_code", "end_date", "ann_date", "trade_date"),
                    status=result.status,
                )
        else:
            previous_status = str(self.store.read_manifest(endpoint.name, partition).get("status", ""))
            changed = previous_status != result.status
            if not check_only and changed:
                self.store.write_status(
                    endpoint.name,
                    partition,
                    status=result.status,
                    metadata=metadata,
                )
        logger.info(
            "Extended {} {}: status={}, rows={}, changed={}",
            endpoint.name,
            partition,
            result.status,
            len(result.data),
            changed,
        )
        return result.status, changed, changed_symbols if changed else set()

    def _pending_tasks(
        self,
        endpoint: ExtendedEndpoint,
        start_date: str,
        end_date: str,
        *,
        force: bool,
        refresh_successful: bool,
        counters: dict[str, int],
    ) -> list[tuple[str, dict[str, str]]]:
        pending: list[tuple[str, dict[str, str]]] = []
        for partition, params in self._tasks(endpoint, start_date, end_date):
            manifest = self.store.read_manifest(endpoint.name, partition)
            status = str(manifest.get("status", ""))
            if force:
                pending.append((partition, params))
            elif refresh_successful and status in {"success", "empty"}:
                pending.append((partition, params))
            elif self.store.is_terminal(endpoint.name, partition):
                counters["skipped"] += 1
            else:
                pending.append((partition, params))
        return pending

    @staticmethod
    def _record_result(
        endpoint: ExtendedEndpoint,
        result: tuple[str, bool, set[str]],
        counters: dict[str, int],
        changed_by_endpoint: dict[str, int],
        changed_symbols_by_endpoint: dict[str, set[str]],
    ) -> None:
        status, changed, changed_symbols = result
        counters[status] = counters.get(status, 0) + 1
        if not changed:
            return
        counters["changed"] += 1
        changed_by_endpoint[endpoint.name] = changed_by_endpoint.get(endpoint.name, 0) + 1
        if changed_symbols:
            changed_symbols_by_endpoint.setdefault(endpoint.name, set()).update(changed_symbols)

    def backfill(
        self,
        start_date: str,
        end_date: str,
        *,
        groups: Iterable[str] | None = None,
        force: bool = False,
        refresh_successful: bool = False,
        check_only: bool = False,
    ) -> dict[str, Any]:
        requested_groups = set(groups or EXTENDED_GROUPS)
        unknown_groups = requested_groups - set(EXTENDED_GROUPS)
        if unknown_groups:
            raise ValueError(f"unknown extended data groups: {sorted(unknown_groups)}")
        counters = {
            "success": 0,
            "empty": 0,
            "permission_denied": 0,
            "failed": 0,
            "skipped": 0,
            "changed": 0,
        }
        changed_by_endpoint: dict[str, int] = {}
        changed_symbols_by_endpoint: dict[str, set[str]] = {}
        started = datetime.now(timezone.utc).isoformat()
        self._write_run_state(
            {
                "status": "running",
                "started_at_utc": started,
                "groups": sorted(requested_groups),
                "max_workers": self.max_workers,
                "check_only": check_only,
            }
        )
        try:
            for endpoint in (item for item in EXTENDED_ENDPOINTS if item.group in requested_groups):
                pending = self._pending_tasks(
                    endpoint,
                    start_date,
                    end_date,
                    force=force,
                    refresh_successful=refresh_successful,
                    counters=counters,
                )
                if endpoint.plan != "symbol" or self.max_workers == 1:
                    for partition, params in pending:
                        result = self._fetch_one(endpoint, partition, params, check_only=check_only)
                        self._record_result(
                            endpoint,
                            result,
                            counters,
                            changed_by_endpoint,
                            changed_symbols_by_endpoint,
                        )
                    continue
                with ThreadPoolExecutor(
                    max_workers=self.max_workers, thread_name_prefix=endpoint.name
                ) as pool:
                    futures = [
                        pool.submit(
                            self._fetch_one,
                            endpoint,
                            partition,
                            params,
                            check_only=check_only,
                        )
                        for partition, params in pending
                    ]
                    for future in as_completed(futures):
                        self._record_result(
                            endpoint,
                            future.result(),
                            counters,
                            changed_by_endpoint,
                            changed_symbols_by_endpoint,
                        )
        except Exception as exc:
            payload = {
                "status": "failed",
                "started_at_utc": started,
                "finished_at_utc": datetime.now(timezone.utc).isoformat(),
                "groups": sorted(requested_groups),
                "max_workers": self.max_workers,
                "check_only": check_only,
                "counters": counters,
                "changed_by_endpoint": changed_by_endpoint,
                "changed_symbols_by_endpoint": {
                    endpoint: sorted(symbols)
                    for endpoint, symbols in changed_symbols_by_endpoint.items()
                },
                "error": str(exc),
            }
            self._write_run_state(payload)
            raise
        payload = {
            "status": "complete",
            "started_at_utc": started,
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
            "groups": sorted(requested_groups),
            "max_workers": self.max_workers,
            "check_only": check_only,
            "counters": counters,
            "changed_by_endpoint": changed_by_endpoint,
            "changed_symbols_by_endpoint": {
                endpoint: sorted(symbols) for endpoint, symbols in changed_symbols_by_endpoint.items()
            },
        }
        self._write_run_state(payload)
        return payload

    def sync_daily(
        self,
        end_date: str,
        *,
        financial_lookback_calendar_days: int = 400,
        check_only: bool = False,
    ) -> dict[str, Any]:
        """Refresh the mutable extended domains needed by the daily research pipeline.

        Trade-date market-reference endpoints are gap-filled across configured
        history without re-fetching terminal partitions. Recent financial report
        periods are deliberately refreshed because announcements and corrections
        can arrive after a quarter-end partition first becomes terminal.
        """

        if financial_lookback_calendar_days < 1:
            raise ValueError("financial_lookback_calendar_days must be positive")
        end = pd.Timestamp(end_date).normalize()
        configured_start = pd.Timestamp(str(self.settings.data["start_date"])).normalize()
        financial_start = max(
            configured_start,
            end - pd.Timedelta(days=financial_lookback_calendar_days),
        )
        market_reference = self.backfill(
            configured_start.strftime("%Y%m%d"),
            end.strftime("%Y%m%d"),
            groups=["market_reference"],
            check_only=check_only,
        )
        financial = self.backfill(
            financial_start.strftime("%Y%m%d"),
            end.strftime("%Y%m%d"),
            groups=["financial"],
            refresh_successful=True,
            check_only=check_only,
        )

        legacy = self.settings.paths.raw / "extended" / "hsgt_moneyflow"
        legacy_removed = False
        if not check_only and legacy.is_dir() and not any(legacy.iterdir()):
            legacy.rmdir()
            legacy_removed = True

        payload = {
            "status": "complete",
            "mode": "daily_sync",
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
            "check_only": check_only,
            "market_reference": market_reference,
            "financial": financial,
            "legacy_hsgt_moneyflow_removed": legacy_removed,
        }
        self._write_run_state(payload)
        return payload
