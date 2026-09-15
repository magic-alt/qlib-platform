from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from qlib_platform.data.certified_daily_sync import CertifiedDailySyncService
from qlib_platform.data.planned_daily_sync import SyncPlanInvalidatedError
from qlib_platform.datasets.dataset_resolver import resolve_dataset


class ResumableCertifiedDailySyncService(CertifiedDailySyncService):
    """Final production sync contract: crash recovery plus monotonic active aliases."""

    def _crash_resume_plan(self, target_session: str, mode: str) -> Path | None:
        if not self.plan_root.is_dir():
            return None
        candidates = sorted(
            self.plan_root.glob("*/plan.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for path in candidates:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if (
                str(payload.get("target_session")) != target_session
                or str(payload.get("mode")) != mode
                or str(payload.get("status")) != "PLANNED"
            ):
                continue
            plan_id = str(payload.get("plan_id") or "")
            if not plan_id:
                continue
            # load_plan also rejects plans created under a different resolved config.
            try:
                self.load_plan(plan_id)
            except (OSError, ValueError, SyncPlanInvalidatedError):
                continue
            state_path = self._apply_state_path(plan_id)
            if not state_path.is_file():
                continue
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            # A hard-killed process leaves RUNNING. Semantic/provider failures are
            # FAILED and intentionally require a new plan unless the operator
            # explicitly chooses --resume.
            if state.get("status") == "RUNNING":
                return path
        return None

    def create_plan(self, *, as_of: str | None = None, mode: str = "routine") -> Path:
        resolved = as_of
        if resolved is None:
            resolved = self._eligible_date(None).strftime("%Y-%m-%d")
        target = pd.Timestamp(resolved).normalize().strftime("%Y%m%d")
        reusable = self._crash_resume_plan(target, mode)
        if reusable is not None:
            return reusable
        return super().create_plan(as_of=resolved, mode=mode)

    def _active_dataset_manifest(self) -> tuple[dict[str, Any], str] | None:
        try:
            resolved = resolve_dataset(self.settings, allow_legacy=False)
            payload = json.loads(resolved.manifest_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError, RuntimeError, json.JSONDecodeError, OSError):
            return None
        return payload, resolved.version_id

    @staticmethod
    def _manifest_end(payload: Mapping[str, Any]) -> pd.Timestamp | None:
        coverage = payload.get("coverage", {})
        if not isinstance(coverage, Mapping):
            return None
        raw = coverage.get("end")
        if not raw:
            return None
        try:
            return pd.Timestamp(str(raw)).normalize()
        except ValueError:
            return None

    def _guard_monotonic_active_alias(self, plan: Mapping[str, Any]) -> None:
        active = self._active_dataset_manifest()
        if active is None:
            return
        payload, _ = active
        sync_context = payload.get("sync_context", {})
        sync_context = sync_context if isinstance(sync_context, Mapping) else {}
        if str(sync_context.get("run_id") or "") == str(plan["plan_id"]):
            return
        active_end = self._manifest_end(payload)
        target = pd.Timestamp(str(plan["target_session"])).normalize()
        if active_end is not None and active_end > target:
            raise SyncPlanInvalidatedError(
                "refusing to resume/publish a stale daily plan behind the active DatasetVersion: "
                f"active_end={active_end.date()} target={target.date()}"
            )

    def _recover_publish_receipt(
        self,
        plan: Mapping[str, Any],
        state: dict[str, Any],
    ) -> bool:
        if self._step_done(state, "qlib_publish"):
            return False
        prerequisites = ("extended_sync", "pit_refresh", "metadata_refresh", "freshness_gate")
        if not all(self._step_done(state, name) for name in prerequisites):
            return False
        active = self._active_dataset_manifest()
        if active is None:
            return False
        payload, version_id = active
        sync_context = payload.get("sync_context", {})
        if not isinstance(sync_context, Mapping):
            return False
        if str(sync_context.get("run_id") or "") != str(plan["plan_id"]):
            return False

        self._write_pending_publish(
            run_id=str(plan["plan_id"]),
            changed_dates=[],
            revised_symbols=set(),
            pit_changed=False,
        )
        self._finish_step(
            state,
            "qlib_publish",
            {
                "result": {
                    "mode": str(payload.get("mode") or "recovered"),
                    "data_release_id": payload.get("data_release_id"),
                    "dataset_version_id": version_id,
                    "recovered_publish_receipt": True,
                }
            },
        )
        return True

    def _extended_and_publish(
        self,
        plan: dict[str, Any],
        state: dict[str, Any],
        *,
        force_full: bool,
    ) -> None:
        self._guard_monotonic_active_alias(plan)
        if self._recover_publish_receipt(plan, state):
            return
        super()._extended_and_publish(plan, state, force_full=force_full)
