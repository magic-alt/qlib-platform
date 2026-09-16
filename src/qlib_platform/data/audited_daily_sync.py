from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from qlib_platform.data.planned_daily_sync import SyncPlanInvalidatedError
from qlib_platform.data.resumable_certified_sync import ResumableCertifiedDailySyncService
from qlib_platform.data.store import sha256_file

CHECKPOINT_CONTRACT_VERSION = "1.0"
SYNC_STEP_ORDER = (
    "market_fetch",
    "factor_reconcile",
    "dividend_fetch",
    "raw_promote",
    "raw_validate",
    "extended_sync",
    "pit_refresh",
    "metadata_refresh",
    "freshness_gate",
    "qlib_publish",
)
_PLAN_IDENTITY_FIELDS = (
    "schema_version",
    "plan_id",
    "mode",
    "target_session",
    "config_sha256",
    "stage_root",
    "required_endpoints",
    "enabled_endpoints",
    "expected_dates",
    "lookback_dates",
    "catchup_dates",
    "fetch_matrix",
    "endpoint_gaps",
    "watermarks",
)


def _json_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class AuditedResumableDailySyncService(ResumableCertifiedDailySyncService):
    """Add integrity-certified checkpoints to the #129 resumable sync path.

    The ingestion algorithm remains the implementation merged in PR #138. This
    subclass only strengthens its persisted checkpoint contract so a completed node
    is reusable when its immutable inputs, semantic output and declared artifacts
    still verify.
    """

    def _stable_plan_identity(self, plan_id: str) -> dict[str, Any]:
        plan = self.load_plan(plan_id)
        return {key: plan.get(key) for key in _PLAN_IDENTITY_FIELDS if key in plan}

    def _step_input_sha256(self, state: Mapping[str, Any], name: str) -> str:
        try:
            index = SYNC_STEP_ORDER.index(name)
        except ValueError:
            index = len(SYNC_STEP_ORDER)
        steps = state.get("steps", {})
        steps = steps if isinstance(steps, Mapping) else {}
        predecessors: dict[str, str] = {}
        for predecessor in SYNC_STEP_ORDER[:index]:
            record = steps.get(predecessor, {})
            if not isinstance(record, Mapping) or record.get("status") != "SUCCEEDED":
                continue
            output = record.get("output", {})
            predecessors[predecessor] = str(record.get("output_sha256") or _json_sha256(output))
        return _json_sha256(
            {
                "plan": self._stable_plan_identity(str(state["plan_id"])),
                "step": name,
                "predecessors": predecessors,
            }
        )

    def _checkpoint_artifacts(self, plan_id: str, name: str) -> dict[str, str]:
        roots: list[Path] = []
        stage_root = self._stage_root(plan_id)
        plan_root = self.plan_root / plan_id
        if name == "market_fetch":
            roots.extend([stage_root / "market", plan_root / "quality"])
        elif name == "factor_reconcile":
            roots.extend([stage_root / "factor_history", stage_root / "market"])
        elif name == "dividend_fetch":
            roots.append(stage_root / "dividend")
        elif name == "raw_validate":
            roots.append(plan_root / "quality" / "raw_store.json")
        elif name == "qlib_publish":
            active = self._active_dataset_manifest()
            if active is not None:
                try:
                    from qlib_platform.datasets.dataset_resolver import resolve_dataset

                    roots.append(resolve_dataset(self.settings, allow_legacy=False).manifest_path)
                except (FileNotFoundError, KeyError, OSError, RuntimeError, ValueError):
                    pass

        artifacts: dict[str, str] = {}
        for root in roots:
            if root.is_file():
                artifacts[str(root)] = sha256_file(root)
                continue
            if not root.is_dir():
                continue
            for path in sorted(value for value in root.rglob("*") if value.is_file()):
                artifacts[str(path)] = sha256_file(path)
        return artifacts

    def _certify_record(self, state: dict[str, Any], name: str) -> None:
        steps = state.setdefault("steps", {})
        record = steps.get(name)
        if not isinstance(record, dict) or record.get("status") != "SUCCEEDED":
            return
        output = record.get("output", {})
        record["checkpoint_contract_version"] = CHECKPOINT_CONTRACT_VERSION
        record["attempt"] = int(record.get("attempt") or state.get("run_attempt") or 1)
        record["input_sha256"] = self._step_input_sha256(state, name)
        record["output_sha256"] = _json_sha256(output)
        record["artifact_sha256"] = self._checkpoint_artifacts(str(state["plan_id"]), name)
        self._save_apply_state(state)

    def _finish_step(self, state: dict[str, Any], name: str, output: Mapping[str, Any]) -> None:
        super()._finish_step(state, name, output)
        self._certify_record(state, name)

    def _verify_raw_promote_output(self, record: Mapping[str, Any]) -> None:
        output = record.get("output", {})
        if not isinstance(output, Mapping):
            raise SyncPlanInvalidatedError("raw_promote checkpoint output is not a mapping")
        changes = output.get("raw_changes", [])
        if not isinstance(changes, list):
            return
        for change in changes:
            if not isinstance(change, Mapping):
                continue
            dataset = str(change.get("dataset") or "")
            trade_date = str(change.get("trade_date") or "")
            expected = change.get("new_content_sha256")
            if not dataset or not trade_date or not expected:
                continue
            current = self._partition_hash(self.store, dataset, trade_date)
            if current != expected:
                raise SyncPlanInvalidatedError(
                    "completed raw_promote checkpoint no longer matches canonical Bronze: "
                    f"{dataset}:{trade_date} expected={expected} current={current}"
                )

    def _verify_checkpoint_record(self, state: dict[str, Any], name: str) -> bool:
        steps = state.get("steps", {})
        record = steps.get(name, {}) if isinstance(steps, Mapping) else {}
        if not isinstance(record, dict) or record.get("status") != "SUCCEEDED":
            return False

        # Upgrade a successful PR #138-era checkpoint in place. We cannot recreate a
        # historical digest that was never persisted, but we can establish an audited
        # baseline before the parent runner is allowed to reuse it.
        if record.get("checkpoint_contract_version") != CHECKPOINT_CONTRACT_VERSION:
            self._certify_record(state, name)
            record = state.get("steps", {}).get(name, {})
            if not isinstance(record, dict):
                return False

        output = record.get("output", {})
        expected_input = self._step_input_sha256(state, name)
        if record.get("input_sha256") != expected_input:
            record["status"] = "INVALIDATED"
            record["invalidated_at_utc"] = datetime.now(timezone.utc).isoformat()
            record["invalidated_reason"] = "input_sha256_changed"
            self._save_apply_state(state)
            return False
        if record.get("output_sha256") != _json_sha256(output):
            raise SyncPlanInvalidatedError(f"completed checkpoint output hash mismatch: {name}")

        artifacts = record.get("artifact_sha256", {})
        if not isinstance(artifacts, Mapping):
            raise SyncPlanInvalidatedError(f"completed checkpoint artifact manifest is invalid: {name}")
        for raw_path, expected_sha in artifacts.items():
            path = Path(str(raw_path))
            if not path.is_file():
                raise SyncPlanInvalidatedError(f"completed checkpoint artifact is missing: {name}: {path}")
            actual_sha = sha256_file(path)
            if actual_sha != expected_sha:
                raise SyncPlanInvalidatedError(f"completed checkpoint artifact hash mismatch: {name}: {path}")

        if name == "raw_promote":
            self._verify_raw_promote_output(record)
        return True

    def _validate_existing_checkpoints(self, state: dict[str, Any]) -> None:
        for name in SYNC_STEP_ORDER:
            steps = state.get("steps", {})
            record = steps.get(name, {}) if isinstance(steps, Mapping) else {}
            if not isinstance(record, Mapping) or record.get("status") != "SUCCEEDED":
                continue
            self._verify_checkpoint_record(state, name)

    def apply_plan(self, plan_id: str, *, force_full: bool = False) -> Path:
        plan = self.load_plan(plan_id)
        if plan.get("status") == "SKIPPED_NON_TRADING_DAY":
            return super().apply_plan(plan_id, force_full=force_full)

        state = self._load_apply_state(plan_id)
        state["checkpoint_contract_version"] = CHECKPOINT_CONTRACT_VERSION
        state["run_attempt"] = int(state.get("run_attempt") or 0) + 1
        state["attempt_started_at_utc"] = datetime.now(timezone.utc).isoformat()
        self._save_apply_state(state)
        self._validate_existing_checkpoints(state)
        return super().apply_plan(plan_id, force_full=force_full)
