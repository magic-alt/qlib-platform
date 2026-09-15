from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from qlib_platform.data.corporate_actions import DIVIDEND_FIELDS
from qlib_platform.data.daily_sync import DailySyncService
from qlib_platform.data.quality import assert_quality, validate_raw_day, validate_raw_store, write_report
from qlib_platform.data.store import PartitionStore, frame_content_sha256
from qlib_platform.runtime.file_lock import FileLock
from qlib_platform.settings import Settings

PLAN_SCHEMA_VERSION = "1.0"
APPLY_STATE_SCHEMA_VERSION = "1.0"
REQUIRED_MARKET_ENDPOINTS = ("daily", "adj_factor", "daily_basic")
_COMPLETE_STEP = "SUCCEEDED"
_SAFE_SYMBOL = re.compile(r"[^A-Za-z0-9_.-]+")


class SyncPlanInvalidatedError(RuntimeError):
    """Raised when canonical inputs changed after a durable sync plan was staged."""


class SyncPlanUnavailableError(RuntimeError):
    """Raised when the local state is insufficient to build a network-free plan."""


def _atomic_json(payload: Mapping[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path


def _json_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _config_hash(settings: Settings) -> str:
    # Hash the fully resolved configuration rather than the overlay file only.  The
    # digest is safe to persist even when a provider password is present because no
    # configuration value is copied into the manifest.
    return _json_hash(settings.data)


def _normalize_date(value: str | pd.Timestamp) -> str:
    return str(pd.Timestamp(value).normalize().strftime("%Y%m%d"))


def _normalize_factor_history(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["ts_code", "trade_date", "adj_factor"])
    required = {"ts_code", "trade_date", "adj_factor"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"adj_factor history is missing columns: {sorted(missing)}")
    result = frame.loc[:, ["ts_code", "trade_date", "adj_factor"]].copy()
    result["ts_code"] = result["ts_code"].astype(str).str.upper().str.strip()
    result["trade_date"] = pd.to_datetime(result["trade_date"], errors="raise").dt.strftime("%Y%m%d")
    result["adj_factor"] = pd.to_numeric(result["adj_factor"], errors="coerce")
    return (
        result.dropna(subset=["ts_code", "trade_date"])
        .drop_duplicates(["ts_code", "trade_date"], keep="last")
        .sort_values(["ts_code", "trade_date"], kind="stable")
        .reset_index(drop=True)
    )


def _changed_symbols(old: pd.DataFrame, new: pd.DataFrame) -> set[str]:
    if "ts_code" not in old and "ts_code" not in new:
        return set()

    def fingerprints(frame: pd.DataFrame) -> dict[str, str]:
        if "ts_code" not in frame:
            return {}
        return {
            str(code).upper(): frame_content_sha256(
                group,
                key_columns=("ts_code", "trade_date"),
            )
            for code, group in frame.groupby("ts_code", dropna=False)
        }

    previous = fingerprints(old)
    current = fingerprints(new)
    return {code for code in previous.keys() | current.keys() if previous.get(code) != current.get(code)}


def factor_history_diff(old: pd.DataFrame, new: pd.DataFrame) -> list[str]:
    """Return only dates whose factor value/row existence changed.

    This is deliberately symbol-oriented.  The old implementation reconciled one
    full-market date partition for every symbol x history-date pair.  A caller now
    compares two compact symbol histories once and patches only this returned set.
    """

    left = _normalize_factor_history(old).rename(columns={"adj_factor": "old_factor"})
    right = _normalize_factor_history(new).rename(columns={"adj_factor": "new_factor"})
    key = ["ts_code", "trade_date"]
    merged = left.merge(right, on=key, how="outer", indicator=True)
    old_factor = pd.to_numeric(merged["old_factor"], errors="coerce")
    new_factor = pd.to_numeric(merged["new_factor"], errors="coerce")
    equal = np.isclose(old_factor, new_factor, rtol=1e-10, atol=1e-12, equal_nan=True)
    changed = merged["_merge"].ne("both") | ~equal
    return sorted(set(merged.loc[changed, "trade_date"].astype(str)))


class PlannedDailySyncService(DailySyncService):
    """Durable delta planner/apply path for production daily ingestion.

    Design rules:
    - planning is local-state only and performs no provider request;
    - provider responses are persisted before downstream work consumes them;
    - a resumed apply reuses staged partitions/calls instead of downloading again;
    - historical factor reconciliation is symbol-oriented and patches only changed dates;
    - the current Bronze/Qlib aliases retain the existing fail-closed publication semantics.
    """

    def __init__(self, settings: Settings, extractor: Any | None = None) -> None:
        super().__init__(settings, extractor=extractor)
        self.plan_root = settings.paths.state / "daily_sync" / "plans"
        self.stage_base = settings.paths.root / "staging" / "daily_sync"
        self.factor_index_root = settings.paths.state / "daily_sync" / "factor_index"

    def _plan_path(self, plan_id: str) -> Path:
        return self.plan_root / plan_id / "plan.json"

    def _apply_state_path(self, plan_id: str) -> Path:
        return self.plan_root / plan_id / "apply_state.json"

    def _stage_root(self, plan_id: str) -> Path:
        return self.stage_base / plan_id

    def _market_stage(self, plan_id: str) -> PartitionStore:
        return PartitionStore(self._stage_root(plan_id) / "market")

    def _dividend_stage(self, plan_id: str) -> PartitionStore:
        return PartitionStore(self._stage_root(plan_id) / "dividend" / "calls")

    def _factor_history_path(self, plan_id: str, symbol: str) -> Path:
        safe = _SAFE_SYMBOL.sub("_", symbol.upper())
        return self._stage_root(plan_id) / "factor_history" / f"{safe}.parquet"

    def _factor_index_path(self, symbol: str) -> Path:
        safe = _SAFE_SYMBOL.sub("_", symbol.upper())
        return self.factor_index_root / f"{safe}.parquet"

    def _factor_index_manifest_path(self, symbol: str) -> Path:
        return self._factor_index_path(symbol).with_suffix(".json")

    def _local_calendar(self) -> pd.DataFrame:
        path = self.settings.paths.metadata / "trade_calendar.parquet"
        if not path.is_file():
            raise SyncPlanUnavailableError(
                "local trade calendar is missing; initialize metadata before production planning"
            )
        frame = pd.read_parquet(path)
        if not {"cal_date", "is_open"}.issubset(frame.columns):
            raise SyncPlanUnavailableError("local trade calendar is missing cal_date/is_open")
        result = frame.copy()
        result["cal_date"] = pd.to_datetime(result["cal_date"], errors="raise").dt.normalize()
        return result

    def _local_open_dates(self, start: str, end: str) -> list[str]:
        frame = self._local_calendar()
        start_ts = pd.Timestamp(start).normalize()
        end_ts = pd.Timestamp(end).normalize()
        dates = frame.loc[
            frame["cal_date"].between(start_ts, end_ts) & frame["is_open"].astype(int).eq(1),
            "cal_date",
        ]
        return sorted(dates.dt.strftime("%Y%m%d").unique().tolist())

    def _calendar_covers(self, target: str) -> bool:
        frame = self._local_calendar()
        return pd.Timestamp(target).normalize() in set(frame["cal_date"])

    def _partition_hash(self, store: PartitionStore, dataset: str, trade_date: str) -> str | None:
        if not store.exists(dataset, trade_date):
            return None
        manifest = store.read_manifest(dataset, trade_date)
        digest = str(manifest.get("content_sha256") or "")
        if digest and manifest.get("content_hash_kind") == "logical_frame_v1":
            return digest
        return frame_content_sha256(store.read(dataset, trade_date), key_columns=("ts_code", "trade_date"))

    def _required_partition_current(self, dataset: str, trade_date: str) -> bool:
        if not self.store.exists(dataset, trade_date):
            return False
        manifest = self.store.read_manifest(dataset, trade_date)
        status = str(manifest.get("status") or "success")
        if status != "success":
            return False
        try:
            return not self.store.read(dataset, trade_date).empty
        except Exception:
            return False

    def _watermarks(self, datasets: Iterable[str]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for dataset in datasets:
            dates = self.store.list_dates(dataset)
            successful = [date for date in dates if self._required_partition_current(dataset, date)]
            result[dataset] = {
                "last_success": successful[-1] if successful else None,
                "partition_count": len(dates),
            }
        return result

    def _enabled_endpoints(self) -> tuple[str, ...]:
        return tuple(endpoint.name for endpoint in self.extractor.endpoints if endpoint.enabled)

    def create_plan(
        self,
        *,
        as_of: str | None = None,
        mode: str = "routine",
    ) -> Path:
        if mode not in {"routine", "historical-audit"}:
            raise ValueError("sync mode must be routine or historical-audit")
        target = _normalize_date(as_of or datetime.now().date().isoformat())
        if not self._calendar_covers(target):
            raise SyncPlanUnavailableError(
                f"local trade calendar does not cover target session {target}; refresh metadata explicitly"
            )
        open_dates = self._local_open_dates(str(self.settings.data["start_date"]), target)
        target_is_open = target in set(open_dates)
        plan_id = f"syncplan-{target}-{uuid.uuid4().hex[:12]}"
        created = datetime.now(timezone.utc).isoformat()
        enabled = self._enabled_endpoints()
        payload: dict[str, Any] = {
            "schema_version": PLAN_SCHEMA_VERSION,
            "plan_id": plan_id,
            "status": "PLANNED" if target_is_open else "SKIPPED_NON_TRADING_DAY",
            "mode": mode,
            "created_at_utc": created,
            "target_session": target,
            "config_sha256": _config_hash(self.settings),
            "provider_calls_during_plan": 0,
            "stage_root": str(self._stage_root(plan_id)),
            "required_endpoints": list(REQUIRED_MARKET_ENDPOINTS),
            "enabled_endpoints": list(enabled),
        }
        if not target_is_open:
            payload.update(
                expected_dates=open_dates,
                fetch_matrix={},
                endpoint_gaps={name: [] for name in REQUIRED_MARKET_ENDPOINTS},
                watermarks=self._watermarks(REQUIRED_MARKET_ENDPOINTS),
            )
            _atomic_json(payload, self._plan_path(plan_id))
            return self._plan_path(plan_id)

        lookback = open_dates[-self.config.market_lookback_trading_days :]
        catchup = open_dates[-self.config.market_catchup_trading_days :]
        endpoint_gaps = {
            dataset: [date for date in catchup if not self._required_partition_current(dataset, date)]
            for dataset in REQUIRED_MARKET_ENDPOINTS
        }
        fetch_matrix: dict[str, list[str]] = {}
        for date in lookback:
            fetch_matrix[date] = list(enabled)
        for dataset, dates in endpoint_gaps.items():
            for date in dates:
                fetch_matrix.setdefault(date, [])
                if dataset not in fetch_matrix[date]:
                    fetch_matrix[date].append(dataset)
        for date in fetch_matrix:
            fetch_matrix[date] = sorted(fetch_matrix[date])

        payload.update(
            expected_dates=open_dates,
            lookback_dates=lookback,
            catchup_dates=catchup,
            fetch_matrix=dict(sorted(fetch_matrix.items())),
            endpoint_gaps=endpoint_gaps,
            watermarks=self._watermarks(REQUIRED_MARKET_ENDPOINTS),
        )
        _atomic_json(payload, self._plan_path(plan_id))
        return self._plan_path(plan_id)

    def load_plan(self, plan_id: str) -> dict[str, Any]:
        path = self._plan_path(plan_id)
        if not path.is_file():
            raise FileNotFoundError(f"unknown daily sync plan: {plan_id}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise TypeError(f"daily sync plan must be a JSON object: {path}")
        if payload.get("schema_version") != PLAN_SCHEMA_VERSION:
            raise ValueError(f"unsupported daily sync plan schema: {payload.get('schema_version')}")
        if payload.get("config_sha256") != _config_hash(self.settings):
            raise SyncPlanInvalidatedError("daily sync plan config hash no longer matches current settings")
        return dict(payload)

    def _save_plan(self, plan: Mapping[str, Any]) -> None:
        _atomic_json(plan, self._plan_path(str(plan["plan_id"])))

    def _load_apply_state(self, plan_id: str) -> dict[str, Any]:
        path = self._apply_state_path(plan_id)
        if path.is_file():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and loaded.get("schema_version") == APPLY_STATE_SCHEMA_VERSION:
                return dict(loaded)
        return {
            "schema_version": APPLY_STATE_SCHEMA_VERSION,
            "plan_id": plan_id,
            "status": "RUNNING",
            "steps": {},
            "context": {
                "changed_trade_dates": [],
                "revised_symbols": [],
                "pit_changed": False,
            },
        }

    def _save_apply_state(self, state: Mapping[str, Any]) -> None:
        _atomic_json(state, self._apply_state_path(str(state["plan_id"])))

    @staticmethod
    def _step_done(state: Mapping[str, Any], name: str) -> bool:
        steps = state.get("steps", {})
        return (
            isinstance(steps, Mapping)
            and isinstance(steps.get(name), Mapping)
            and steps[name].get("status") == _COMPLETE_STEP
        )

    def _finish_step(self, state: dict[str, Any], name: str, output: Mapping[str, Any]) -> None:
        steps = state.setdefault("steps", {})
        steps[name] = {
            "status": _COMPLETE_STEP,
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
            "output": dict(output),
        }
        self._save_apply_state(state)

    def _base_metadata(self, dataset: str, trade_date: str, frame: pd.DataFrame) -> dict[str, Any]:
        current = self.store.read(dataset, trade_date)
        base_hash = self._partition_hash(self.store, dataset, trade_date)
        return {
            "base_exists": self.store.exists(dataset, trade_date),
            "base_content_sha256": base_hash,
            "planned_content_sha256": frame_content_sha256(frame, key_columns=("ts_code", "trade_date")),
            "planned_changed_symbols": sorted(_changed_symbols(current, frame)),
        }

    def _stage_market_frame(
        self,
        stage: PartitionStore,
        dataset: str,
        trade_date: str,
        frame: pd.DataFrame,
        metadata: Mapping[str, Any],
    ) -> None:
        previous_stage = stage.read_manifest(dataset, trade_date)
        base = {
            key: previous_stage.get(key)
            for key in ("base_exists", "base_content_sha256")
            if key in previous_stage
        }
        if not base:
            base = self._base_metadata(dataset, trade_date, frame)
        else:
            current = self.store.read(dataset, trade_date)
            base.update(
                planned_content_sha256=frame_content_sha256(frame, key_columns=("ts_code", "trade_date")),
                planned_changed_symbols=sorted(_changed_symbols(current, frame)),
            )
        promoted = dict(metadata)
        promoted.update(base)
        stage.write(dataset, trade_date, frame, promoted)

    def _fetch_market_to_stage(
        self,
        plan: dict[str, Any],
        state: dict[str, Any],
    ) -> None:
        if self._step_done(state, "market_fetch"):
            return
        stage = self._market_stage(str(plan["plan_id"]))
        provider_calls = 0
        fetch_matrix = plan.get("fetch_matrix", {})
        if not isinstance(fetch_matrix, Mapping):
            raise ValueError("daily sync plan fetch_matrix must be a mapping")
        endpoint_by_name = {endpoint.name: endpoint for endpoint in self.extractor.endpoints}
        for trade_date, endpoint_names_raw in fetch_matrix.items():
            endpoint_names = [str(value) for value in endpoint_names_raw]
            for name in endpoint_names:
                endpoint = endpoint_by_name[name]
                # A successfully staged response is a checkpoint.  This is the key
                # property that prevents provider calls from repeating after a crash.
                if stage.is_terminal(name, str(trade_date)):
                    continue
                params: dict[str, str] = {"trade_date": str(trade_date)}
                if name == "suspend_d":
                    params["suspend_type"] = "S"
                result = self.extractor.client.fetch(
                    name,
                    fields=endpoint.fields,
                    required=endpoint.required,
                    **params,
                )
                provider_calls += 1
                metadata = {
                    "api": name,
                    "trade_date": str(trade_date),
                    "attempts": result.attempts,
                    "params": params,
                    "sync_mode": "planned_daily_fetch",
                    "plan_id": plan["plan_id"],
                }
                if not result.succeeded:
                    if endpoint.required:
                        raise RuntimeError(
                            f"required endpoint failed during staged fetch: {name} {trade_date}"
                        )
                    stage.write_status(name, str(trade_date), status=result.status, metadata=metadata)
                    continue
                if endpoint.required and result.data.empty:
                    raise RuntimeError(
                        f"required endpoint returned empty during staged fetch: {name} {trade_date}"
                    )
                self._stage_market_frame(stage, name, str(trade_date), result.data.copy(), metadata)

            validation: dict[str, pd.DataFrame] = {}
            for required in REQUIRED_MARKET_ENDPOINTS:
                if stage.exists(required, str(trade_date)):
                    validation[required] = stage.read(required, str(trade_date))
                else:
                    validation[required] = self.store.read(required, str(trade_date))
            report = validate_raw_day(validation, str(trade_date))
            assert_quality(report)
            write_report(
                report,
                self.plan_root / str(plan["plan_id"]) / "quality" / f"raw_{trade_date}.json",
            )
        self._finish_step(
            state,
            "market_fetch",
            {
                "provider_calls": provider_calls,
                "staged_dates": sorted(str(value) for value in fetch_matrix),
            },
        )

    def _factor_frame_for_date(self, plan_id: str, trade_date: str) -> pd.DataFrame:
        stage = self._market_stage(plan_id)
        if stage.exists("adj_factor", trade_date):
            return stage.read("adj_factor", trade_date)
        return self.store.read("adj_factor", trade_date)

    def _planned_factor_event_symbols(self, plan: Mapping[str, Any]) -> set[str]:
        dates = [str(value) for value in plan.get("expected_dates", [])]
        if len(dates) < 2:
            return set()
        previous = self._factor_frame_for_date(str(plan["plan_id"]), dates[-2])
        current = self._factor_frame_for_date(str(plan["plan_id"]), dates[-1])
        if previous.empty or current.empty:
            return set()
        left = previous[["ts_code", "adj_factor"]].rename(columns={"adj_factor": "previous"})
        right = current[["ts_code", "adj_factor"]].rename(columns={"adj_factor": "current"})
        paired = left.merge(right, on="ts_code", how="inner")
        prior = pd.to_numeric(paired["previous"], errors="coerce")
        latest = pd.to_numeric(paired["current"], errors="coerce")
        changed = ~(np.isclose(prior, latest, rtol=1e-10, atol=1e-12) | (prior.isna() & latest.isna()))
        return set(paired.loc[changed, "ts_code"].astype(str).str.upper())

    def _latest_factor_symbols(self, plan: Mapping[str, Any]) -> set[str]:
        dates = [str(value) for value in plan.get("expected_dates", [])]
        if not dates:
            return set()
        frame = self._factor_frame_for_date(str(plan["plan_id"]), dates[-1])
        return set(frame.get("ts_code", pd.Series(dtype=str)).dropna().astype(str).str.upper())

    def _write_factor_index(self, symbol: str, history: pd.DataFrame, indexed_through: str) -> None:
        normalized = _normalize_factor_history(history)
        path = self._factor_index_path(symbol)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".parquet.tmp")
        normalized.to_parquet(temporary, index=False)
        os.replace(temporary, path)
        _atomic_json(
            {
                "schema_version": "1.0",
                "symbol": symbol.upper(),
                "indexed_through": indexed_through,
                "content_sha256": frame_content_sha256(normalized, key_columns=("ts_code", "trade_date")),
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            },
            self._factor_index_manifest_path(symbol),
        )

    def _load_factor_histories(
        self,
        symbols: set[str],
        *,
        staged_plan_id: str,
    ) -> tuple[dict[str, pd.DataFrame], int]:
        """Load/build symbol indexes with at most one pass over date partitions.

        Missing symbol indexes are constructed together.  With 14 event symbols and
        2,500 date partitions this performs ~2,500 partition reads, not 35,000.
        """

        normalized_symbols = {value.upper() for value in symbols}
        histories: dict[str, pd.DataFrame] = {}
        missing: set[str] = set()
        for symbol in normalized_symbols:
            path = self._factor_index_path(symbol)
            if path.is_file():
                histories[symbol] = _normalize_factor_history(pd.read_parquet(path))
            else:
                missing.add(symbol)

        partition_reads = 0
        if missing:
            buckets: dict[str, list[pd.DataFrame]] = {symbol: [] for symbol in missing}
            for trade_date in self.store.list_dates("adj_factor"):
                frame = self.store.read("adj_factor", trade_date)
                partition_reads += 1
                if frame.empty or "ts_code" not in frame:
                    continue
                codes = frame["ts_code"].astype(str).str.upper()
                selected = frame.loc[codes.isin(missing), ["ts_code", "trade_date", "adj_factor"]]
                if selected.empty:
                    continue
                for symbol, group in selected.groupby(selected["ts_code"].astype(str).str.upper()):
                    buckets[str(symbol)].append(group.copy())
            indexed_through = (
                self.store.list_dates("adj_factor")[-1] if self.store.list_dates("adj_factor") else ""
            )
            for symbol in missing:
                history = (
                    pd.concat(buckets[symbol], ignore_index=True)
                    if buckets[symbol]
                    else pd.DataFrame(columns=["ts_code", "trade_date", "adj_factor"])
                )
                histories[symbol] = _normalize_factor_history(history)
                self._write_factor_index(symbol, histories[symbol], indexed_through)

        # The canonical cache intentionally trails the plan.  Overlay staged rows so
        # a brand-new target date is not misclassified as a historical revision.
        stage = self._market_stage(staged_plan_id)
        staged_dates = set(stage.list_dates("adj_factor"))
        for symbol in normalized_symbols:
            additions: list[pd.DataFrame] = []
            for trade_date in staged_dates:
                frame = stage.read("adj_factor", trade_date)
                if frame.empty or "ts_code" not in frame:
                    continue
                selected = frame.loc[
                    frame["ts_code"].astype(str).str.upper().eq(symbol),
                    ["ts_code", "trade_date", "adj_factor"],
                ]
                if not selected.empty:
                    additions.append(selected)
            if additions:
                histories[symbol] = _normalize_factor_history(
                    pd.concat([histories[symbol], *additions], ignore_index=True)
                )
        return histories, partition_reads

    def _load_or_fetch_remote_factor_history(
        self,
        plan: Mapping[str, Any],
        symbol: str,
    ) -> tuple[pd.DataFrame, bool]:
        path = self._factor_history_path(str(plan["plan_id"]), symbol)
        if path.is_file():
            return _normalize_factor_history(pd.read_parquet(path)), False
        path.parent.mkdir(parents=True, exist_ok=True)
        history = self.extractor.client.call(
            "adj_factor",
            fields="ts_code,trade_date,adj_factor",
            required=True,
            ts_code=symbol,
            start_date=str(self.settings.data["start_date"]),
            end_date=str(plan["target_session"]),
        )
        if history.empty:
            raise RuntimeError(f"adj_factor history is empty for {symbol}")
        normalized = _normalize_factor_history(history)
        temporary = path.with_suffix(".parquet.tmp")
        normalized.to_parquet(temporary, index=False)
        os.replace(temporary, path)
        return normalized, True

    def _stage_factor_repairs(
        self,
        plan: Mapping[str, Any],
        remote_histories: Mapping[str, pd.DataFrame],
        changed_by_symbol: Mapping[str, list[str]],
    ) -> list[str]:
        repairs_by_date: dict[str, set[str]] = {}
        for symbol, dates in changed_by_symbol.items():
            for trade_date in dates:
                repairs_by_date.setdefault(str(trade_date), set()).add(symbol.upper())
        stage = self._market_stage(str(plan["plan_id"]))
        for trade_date, symbols in sorted(repairs_by_date.items()):
            current = (
                stage.read("adj_factor", trade_date)
                if stage.exists("adj_factor", trade_date)
                else self.store.read("adj_factor", trade_date)
            )
            if not current.empty and "ts_code" not in current:
                raise ValueError(f"stored adj_factor partition is missing ts_code: {trade_date}")
            retained = (
                current.loc[~current["ts_code"].astype(str).str.upper().isin(symbols)].copy()
                if not current.empty
                else current
            )
            additions: list[pd.DataFrame] = []
            for symbol in sorted(symbols):
                remote = remote_histories[symbol]
                rows = remote.loc[
                    remote["trade_date"].astype(str).eq(trade_date),
                    ["ts_code", "trade_date", "adj_factor"],
                ]
                if not rows.empty:
                    additions.append(rows)
            merged = _normalize_factor_history(
                pd.concat([retained, *additions], ignore_index=True) if additions else retained
            )
            self._stage_market_frame(
                stage,
                "adj_factor",
                trade_date,
                merged,
                {
                    "api": "adj_factor",
                    "trade_date": trade_date,
                    "params": {"symbols": sorted(symbols)},
                    "sync_mode": "targeted_factor_repair",
                    "plan_id": plan["plan_id"],
                },
            )
        return sorted(repairs_by_date)

    def _reconcile_factors(
        self,
        plan: dict[str, Any],
        state: dict[str, Any],
    ) -> None:
        if self._step_done(state, "factor_reconcile"):
            return
        event_symbols = self._planned_factor_event_symbols(plan)
        if plan.get("mode") == "historical-audit":
            event_symbols |= self._latest_factor_symbols(plan)
        local_histories, partition_reads = self._load_factor_histories(
            event_symbols,
            staged_plan_id=str(plan["plan_id"]),
        )
        remote_histories: dict[str, pd.DataFrame] = {}
        changed_by_symbol: dict[str, list[str]] = {}
        provider_calls = 0
        for symbol in sorted(event_symbols):
            remote, fetched = self._load_or_fetch_remote_factor_history(plan, symbol)
            provider_calls += int(fetched)
            remote_histories[symbol] = remote
            changed_by_symbol[symbol] = factor_history_diff(local_histories[symbol], remote)
        repair_dates = self._stage_factor_repairs(plan, remote_histories, changed_by_symbol)
        plan["factor_event_symbols"] = sorted(event_symbols)
        plan["factor_repair_dates"] = repair_dates
        plan["factor_changed_by_symbol"] = {
            symbol: dates for symbol, dates in changed_by_symbol.items() if dates
        }
        self._save_plan(plan)
        self._finish_step(
            state,
            "factor_reconcile",
            {
                "provider_calls": provider_calls,
                "event_symbol_count": len(event_symbols),
                "partition_reads_for_index_build": partition_reads,
                "repair_dates": repair_dates,
                "changed_symbol_count": sum(bool(value) for value in changed_by_symbol.values()),
            },
        )

    def _dividend_call(
        self,
        plan: Mapping[str, Any],
        parameter: str,
        value: str,
    ) -> tuple[pd.DataFrame, bool]:
        stage = self._dividend_stage(str(plan["plan_id"]))
        dataset = parameter
        if stage.is_terminal(dataset, value):
            return stage.read(dataset, value), False
        kwargs = {parameter: value}
        frame = self.extractor.client.call(
            "dividend",
            fields=DIVIDEND_FIELDS,
            required=True,
            **kwargs,
        )
        status = "empty" if frame.empty else "success"
        stage.write(
            dataset,
            value,
            frame,
            {
                "api": "dividend",
                "params": kwargs,
                "sync_mode": "planned_daily_dividend",
                "plan_id": plan["plan_id"],
            },
            status=status,
        )
        return frame, True

    def _stage_dividends(self, plan: dict[str, Any], state: dict[str, Any]) -> None:
        if self._step_done(state, "dividend_fetch"):
            return
        end = pd.Timestamp(str(plan["target_session"])).normalize()
        dates = pd.date_range(
            end=end,
            periods=self.config.corporate_action_lookback_calendar_days,
            freq="D",
        )
        frames: list[pd.DataFrame] = []
        provider_calls = 0
        for date in dates:
            key = date.strftime("%Y%m%d")
            for parameter in ("ann_date", "imp_ann_date", "ex_date"):
                frame, fetched = self._dividend_call(plan, parameter, key)
                provider_calls += int(fetched)
                if not frame.empty:
                    frames.append(frame)
        for symbol in sorted(set(plan.get("factor_event_symbols", []))):
            frame, fetched = self._dividend_call(plan, "ts_code", str(symbol))
            provider_calls += int(fetched)
            if not frame.empty:
                frames.append(frame)
        incoming = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        incoming_path = self._stage_root(str(plan["plan_id"])) / "dividend" / "incoming.parquet"
        incoming_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = incoming_path.with_suffix(".parquet.tmp")
        incoming.to_parquet(temporary, index=False)
        os.replace(temporary, incoming_path)
        preview = self.actions.upsert(incoming, check_only=True)
        plan["dividend_changed_symbols"] = preview["changed_symbols"]
        self._save_plan(plan)
        self._finish_step(
            state,
            "dividend_fetch",
            {
                "provider_calls": provider_calls,
                "fetched_rows": len(incoming),
                "changed_symbol_count": preview["changed_symbol_count"],
            },
        )

    def _stage_targets(self, plan: Mapping[str, Any]) -> list[tuple[str, str]]:
        targets: set[tuple[str, str]] = set()
        matrix = plan.get("fetch_matrix", {})
        if isinstance(matrix, Mapping):
            for trade_date, endpoints in matrix.items():
                for endpoint in endpoints:
                    targets.add((str(endpoint), str(trade_date)))
        for trade_date in plan.get("factor_repair_dates", []):
            targets.add(("adj_factor", str(trade_date)))
        stage = self._market_stage(str(plan["plan_id"]))
        return sorted(target for target in targets if stage.exists(*target))

    def _validate_stage_bases(self, plan: Mapping[str, Any]) -> None:
        stage = self._market_stage(str(plan["plan_id"]))
        conflicts: list[str] = []
        for dataset, trade_date in self._stage_targets(plan):
            staged_manifest = stage.read_manifest(dataset, trade_date)
            expected_base = staged_manifest.get("base_content_sha256")
            staged_hash = str(staged_manifest.get("content_sha256") or "")
            current_hash = self._partition_hash(self.store, dataset, trade_date)
            # current == staged means a previous attempt promoted the partition and
            # crashed before checkpointing.  Treat it as idempotently complete.
            if current_hash not in {expected_base, staged_hash}:
                conflicts.append(
                    f"{dataset}:{trade_date}:expected_base={expected_base}:current={current_hash}"
                )
        if conflicts:
            raise SyncPlanInvalidatedError(
                "canonical partitions changed after the plan was staged: " + "; ".join(conflicts[:10])
            )

    def _planned_raw_change_summary(
        self, plan: Mapping[str, Any]
    ) -> tuple[list[str], set[str], list[dict[str, Any]]]:
        stage = self._market_stage(str(plan["plan_id"]))
        changed_dates: set[str] = set()
        revised_symbols: set[str] = set()
        changes: list[dict[str, Any]] = []
        for dataset, trade_date in self._stage_targets(plan):
            manifest = stage.read_manifest(dataset, trade_date)
            base_hash = manifest.get("base_content_sha256")
            new_hash = manifest.get("content_sha256")
            if base_hash == new_hash:
                continue
            changed_dates.add(trade_date)
            symbols = [str(value) for value in manifest.get("planned_changed_symbols", [])]
            if bool(manifest.get("base_exists")):
                revised_symbols.update(symbols)
            changes.append(
                {
                    "dataset": dataset,
                    "trade_date": trade_date,
                    "old_content_sha256": base_hash,
                    "new_content_sha256": new_hash,
                    "changed_symbol_count": len(symbols),
                }
            )
        return sorted(changed_dates), revised_symbols, changes

    def _promote_staged_raw(
        self,
        plan: dict[str, Any],
        state: dict[str, Any],
    ) -> None:
        if self._step_done(state, "raw_promote"):
            return
        self._validate_stage_bases(plan)
        stage = self._market_stage(str(plan["plan_id"]))
        staged: dict[tuple[str, str], pd.DataFrame] = {}
        metadata: dict[tuple[str, str], dict[str, Any]] = {}
        for target in self._stage_targets(plan):
            staged[target] = stage.read(*target)
            metadata[target] = stage.read_manifest(*target)

        planned_dates, planned_revised, planned_changes = self._planned_raw_change_summary(plan)
        actual_dates, actual_revised, _ = self._promote_raw(staged, metadata, check_only=False)
        changed_dates = sorted(set(planned_dates) | set(actual_dates))
        revised_symbols = set(planned_revised) | set(actual_revised)

        incoming_path = self._stage_root(str(plan["plan_id"])) / "dividend" / "incoming.parquet"
        incoming = pd.read_parquet(incoming_path) if incoming_path.is_file() else pd.DataFrame()
        dividend = self.actions.upsert(incoming, check_only=False)

        pending = self._load_pending_publish()
        if pending:
            changed_dates = sorted(
                set(changed_dates) | {str(value) for value in pending.get("changed_trade_dates", [])}
            )
            revised_symbols.update(str(value) for value in pending.get("revised_symbols", []))
        pit_changed = bool(pending.get("pit_changed", False)) if pending else False
        self._write_pending_publish(
            run_id=str(plan["plan_id"]),
            changed_dates=changed_dates,
            revised_symbols=revised_symbols,
            pit_changed=pit_changed,
        )
        state["context"] = {
            "changed_trade_dates": changed_dates,
            "revised_symbols": sorted(revised_symbols),
            "pit_changed": pit_changed,
        }
        self._finish_step(
            state,
            "raw_promote",
            {
                "changed_trade_dates": changed_dates,
                "revised_symbols": sorted(revised_symbols),
                "raw_changes": planned_changes,
                "dividend": dividend,
            },
        )

        # Remote symbol histories are authoritative for the cache only after Bronze
        # promotion succeeded.  A failed plan therefore cannot poison the index.
        for symbol in set(plan.get("factor_event_symbols", [])):
            path = self._factor_history_path(str(plan["plan_id"]), str(symbol))
            if path.is_file():
                self._write_factor_index(
                    str(symbol),
                    pd.read_parquet(path),
                    str(plan["target_session"]),
                )

    def _validate_raw(self, plan: Mapping[str, Any], state: dict[str, Any]) -> None:
        if self._step_done(state, "raw_validate"):
            return
        context = state["context"]
        deep_dates = sorted(
            set(str(value) for value in plan.get("fetch_matrix", {}))
            | set(str(value) for value in context.get("changed_trade_dates", []))
        )
        report = validate_raw_store(
            self.store,
            expected_dates=[str(value) for value in plan.get("expected_dates", [])],
            deep_dates=deep_dates,
        )
        write_report(
            report,
            self.plan_root / str(plan["plan_id"]) / "quality" / "raw_store.json",
        )
        assert_quality(report)
        self._finish_step(state, "raw_validate", {"passed": True, "deep_dates": deep_dates})

    def _freshness_gate(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        target = str(plan["target_session"])
        failures: list[str] = []
        endpoint_status: dict[str, Any] = {}
        for dataset in REQUIRED_MARKET_ENDPOINTS:
            frame = self.store.read(dataset, target)
            manifest = self.store.read_manifest(dataset, target)
            ok = (
                self.store.exists(dataset, target) and not frame.empty and manifest.get("status") == "success"
            )
            endpoint_status[dataset] = {
                "fresh": ok,
                "rows": len(frame),
                "content_sha256": manifest.get("content_sha256"),
            }
            if not ok:
                failures.append(f"{dataset}:{target}")

        benchmark = str(self.settings.data.get("research", {}).get("benchmark") or "SH000300")
        benchmark_path = self.settings.paths.metadata / "benchmarks" / f"{benchmark.upper()}.parquet"
        benchmark_fresh = False
        if benchmark_path.is_file():
            benchmark_frame = pd.read_parquet(benchmark_path)
            if "trade_date" in benchmark_frame:
                benchmark_dates = pd.to_datetime(benchmark_frame["trade_date"], errors="coerce").dt.strftime(
                    "%Y%m%d"
                )
                benchmark_fresh = target in set(benchmark_dates.dropna())
        if not benchmark_fresh:
            failures.append(f"benchmark:{benchmark}:{target}")

        return {
            "passed": not failures,
            "target_session": target,
            "required_endpoints": endpoint_status,
            "benchmark": {"symbol": benchmark, "fresh": benchmark_fresh},
            "failures": failures,
        }

    def _extended_and_publish(
        self,
        plan: dict[str, Any],
        state: dict[str, Any],
        *,
        force_full: bool,
    ) -> None:
        context = state["context"]
        changed_dates = [str(value) for value in context.get("changed_trade_dates", [])]
        revised_symbols = set(str(value) for value in context.get("revised_symbols", []))
        pit_changed = bool(context.get("pit_changed", False))
        target_ts = pd.Timestamp(str(plan["target_session"]))

        if not self._step_done(state, "extended_sync"):
            extended = self._sync_extended(target_ts)
            self._finish_step(state, "extended_sync", {"result": extended})
        else:
            extended = state["steps"]["extended_sync"]["output"].get("result")

        if not self._step_done(state, "pit_refresh"):
            pit_result, newly_changed_pit, pit_symbols = self._refresh_pit_from_extended()
            if newly_changed_pit:
                pit_changed = True
                revised_symbols.update(pit_symbols)
            context.update(
                revised_symbols=sorted(revised_symbols),
                pit_changed=pit_changed,
            )
            self._write_pending_publish(
                run_id=str(plan["plan_id"]),
                changed_dates=changed_dates,
                revised_symbols=revised_symbols,
                pit_changed=pit_changed,
            )
            self._finish_step(state, "pit_refresh", {"result": pit_result, "changed": pit_changed})
        else:
            pit_result = state["steps"]["pit_refresh"]["output"].get("result")
            pit_changed = bool(context.get("pit_changed", False))
            revised_symbols = set(str(value) for value in context.get("revised_symbols", []))

        if not self._step_done(state, "metadata_refresh"):
            fetch_dates = sorted(str(value) for value in plan.get("fetch_matrix", {}))
            metadata = self._refresh_metadata(fetch_dates or [str(plan["target_session"])])
            self._finish_step(state, "metadata_refresh", {"result": metadata})
        else:
            metadata = state["steps"]["metadata_refresh"]["output"].get("result")

        if not self._step_done(state, "freshness_gate"):
            freshness = self._freshness_gate(plan)
            if not freshness["passed"]:
                raise RuntimeError(
                    "daily freshness gate blocked publication: " + ", ".join(freshness["failures"])
                )
            self._finish_step(state, "freshness_gate", freshness)

        if not self._step_done(state, "qlib_publish"):
            sync_context: dict[str, object] = {
                "run_id": str(plan["plan_id"]),
                "eligible_date": pd.Timestamp(str(plan["target_session"])).strftime("%Y-%m-%d"),
                "changed_trade_dates": changed_dates,
                "revised_symbols": sorted(revised_symbols),
                "factor_event_symbols": list(plan.get("factor_event_symbols", [])),
                "dividend_changed_symbols": list(plan.get("dividend_changed_symbols", [])),
                "extended": extended,
                "pit": pit_result,
                "pit_changed": pit_changed,
                "metadata": metadata,
                "sync_plan_id": plan["plan_id"],
            }
            qlib_result = self._publish_qlib(
                changed_dates,
                revised_symbols,
                force_full=force_full,
                pit_changed=pit_changed,
                sync_context=sync_context,
            )
            self._write_pending_publish(
                run_id=str(plan["plan_id"]),
                changed_dates=[],
                revised_symbols=set(),
                pit_changed=False,
            )
            self._finish_step(state, "qlib_publish", {"result": qlib_result})

    def _write_watermarks(self, plan: Mapping[str, Any]) -> None:
        payload = {
            "schema_version": "1.0",
            "target_session": plan["target_session"],
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "datasets": self._watermarks(REQUIRED_MARKET_ENDPOINTS),
            "plan_id": plan["plan_id"],
        }
        _atomic_json(payload, self.settings.paths.state / "daily_sync" / "watermarks.json")

    def apply_plan(self, plan_id: str, *, force_full: bool = False) -> Path:
        plan = self.load_plan(plan_id)
        if plan.get("status") == "SKIPPED_NON_TRADING_DAY":
            return self._plan_path(plan_id)
        lock_path = self.settings.paths.state / "daily_sync" / "daily_sync.lock"
        with FileLock(
            lock_path,
            blocking=False,
            unavailable_message="daily sync is already running",
        ):
            state = self._load_apply_state(plan_id)
            try:
                self._fetch_market_to_stage(plan, state)
                self._reconcile_factors(plan, state)
                self._stage_dividends(plan, state)
                self._promote_staged_raw(plan, state)
                self._validate_raw(plan, state)
                self._extended_and_publish(plan, state, force_full=force_full)
                state["status"] = "SUCCEEDED"
                state["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
                self._save_apply_state(state)
                self._write_watermarks(plan)
                plan["status"] = "APPLIED"
                plan["finished_at_utc"] = state["finished_at_utc"]
                self._save_plan(plan)
                return self._apply_state_path(plan_id)
            except Exception as exc:
                state["status"] = "FAILED"
                state["error_type"] = type(exc).__name__
                state["error"] = str(exc)
                state["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
                self._save_apply_state(state)
                raise


def create_daily_sync_plan(
    settings: Settings,
    *,
    as_of: str | None = None,
    mode: str = "routine",
) -> Path:
    return PlannedDailySyncService(settings).create_plan(as_of=as_of, mode=mode)


def apply_daily_sync_plan(
    settings: Settings,
    plan_id: str,
    *,
    force_full: bool = False,
) -> Path:
    return PlannedDailySyncService(settings).apply_plan(plan_id, force_full=force_full)
