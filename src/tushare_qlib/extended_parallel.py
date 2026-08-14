"""Concurrent execution strategy for the wide Tushare data catalogue."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Iterable

from loguru import logger

from .client import FetchResult
from .extended_data import EXTENDED_ENDPOINTS, EXTENDED_GROUPS, ExtendedDataBackfill, ExtendedEndpoint


class FastExtendedDataBackfill(ExtendedDataBackfill):
    """Use all-market batches first and concurrent calls only where unavoidable.

    Symbol-history APIs do not expose an all-market range query.  Those calls
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

    def _fetch_one(self, endpoint: ExtendedEndpoint, partition: str, params: dict[str, str]) -> str:
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
        if result.succeeded:
            self.store.write(endpoint.name, partition, result.data, metadata, status=result.status)
        else:
            self.store.write_status(endpoint.name, partition, status=result.status, metadata=metadata)
        logger.info(
            "Extended {} {}: status={}, rows={}", endpoint.name, partition, result.status, len(result.data)
        )
        return result.status

    def _pending_tasks(
        self,
        endpoint: ExtendedEndpoint,
        start_date: str,
        end_date: str,
        *,
        force: bool,
        counters: dict[str, int],
    ) -> list[tuple[str, dict[str, str]]]:
        pending: list[tuple[str, dict[str, str]]] = []
        for partition, params in self._tasks(endpoint, start_date, end_date):
            if not force and self.store.is_terminal(endpoint.name, partition):
                counters["skipped"] += 1
            else:
                pending.append((partition, params))
        return pending

    def backfill(
        self,
        start_date: str,
        end_date: str,
        *,
        groups: Iterable[str] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        requested_groups = set(groups or EXTENDED_GROUPS)
        unknown_groups = requested_groups - set(EXTENDED_GROUPS)
        if unknown_groups:
            raise ValueError(f"unknown extended data groups: {sorted(unknown_groups)}")
        counters = {"success": 0, "empty": 0, "permission_denied": 0, "failed": 0, "skipped": 0}
        started = datetime.now(timezone.utc).isoformat()
        self._write_run_state(
            {
                "status": "running",
                "started_at_utc": started,
                "groups": sorted(requested_groups),
                "max_workers": self.max_workers,
            }
        )
        try:
            for endpoint in (item for item in EXTENDED_ENDPOINTS if item.group in requested_groups):
                pending = self._pending_tasks(endpoint, start_date, end_date, force=force, counters=counters)
                if endpoint.plan != "symbol" or self.max_workers == 1:
                    for partition, params in pending:
                        status = self._fetch_one(endpoint, partition, params)
                        counters[status] = counters.get(status, 0) + 1
                    continue
                with ThreadPoolExecutor(
                    max_workers=self.max_workers, thread_name_prefix=endpoint.name
                ) as pool:
                    futures = [
                        pool.submit(self._fetch_one, endpoint, partition, params)
                        for partition, params in pending
                    ]
                    for future in as_completed(futures):
                        status = future.result()
                        counters[status] = counters.get(status, 0) + 1
        except Exception as exc:
            payload = {
                "status": "failed",
                "started_at_utc": started,
                "finished_at_utc": datetime.now(timezone.utc).isoformat(),
                "groups": sorted(requested_groups),
                "max_workers": self.max_workers,
                "counters": counters,
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
            "counters": counters,
        }
        self._write_run_state(payload)
        return payload
