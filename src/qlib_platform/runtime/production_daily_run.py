from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from qlib_platform.data.audited_daily_sync import (
    CHECKPOINT_CONTRACT_VERSION,
    AuditedResumableDailySyncService,
)
from qlib_platform.data.store import sha256_file
from qlib_platform.runtime import daily_research_run as base
from qlib_platform.settings import Settings

DAILY_RUN_CONTRACT_VERSION = "1.1"


def _code_provenance(settings: Settings) -> dict[str, str | None]:
    try:
        package_version = version("qlib-platform")
    except PackageNotFoundError:
        package_version = "source-checkout"

    commit = os.getenv("QLIB_PLATFORM_GIT_SHA", "").strip() or os.getenv("GITHUB_SHA", "").strip() or None
    if commit is None:
        repository = settings.config_path.parent.parent
        try:
            completed = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repository,
                capture_output=True,
                text=True,
                check=False,
                timeout=2.0,
            )
        except (OSError, subprocess.SubprocessError):
            completed = None
        if completed is not None and completed.returncode == 0:
            value = completed.stdout.strip()
            commit = value or None
    return {
        "git_commit": commit,
        "package_version": package_version,
    }


def _artifact_hashes(output: Mapping[str, Any]) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    for key in ("apply_state", "path", "stdout", "stderr"):
        raw = output.get(key)
        if not raw:
            continue
        path = Path(str(raw))
        if path.is_file():
            artifacts[str(path)] = sha256_file(path)
    return artifacts


def _record_summary(record: Mapping[str, Any]) -> dict[str, Any]:
    output = record.get("output", {})
    output = output if isinstance(output, Mapping) else {}
    return {
        "status": record.get("status"),
        "attempt": int(record.get("attempt") or 0),
        "input_sha256": str(record.get("input_sha256") or ""),
        "output_sha256": str(record.get("output_sha256") or base._identity(output)),
        "artifact_sha256": dict(record.get("artifact_sha256") or {}),
        "error": record.get("error"),
    }


class DailyResearchRun(base.DailyResearchRun):
    """Daily DAG bound to resumable ingestion and certified freshness gates."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.sync: AuditedResumableDailySyncService = AuditedResumableDailySyncService(settings)
        self.root = settings.paths.state / "daily_run"

    def _load_state(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        state = super()._load_state(plan)
        state.setdefault("code", _code_provenance(self.settings))
        state.setdefault("config_path", str(self.settings.config_path))
        state.setdefault("config_sha256", str(plan.get("config_sha256") or ""))
        state.setdefault("provider_watermarks", plan.get("watermarks", {}))
        state.setdefault("endpoint_gaps", plan.get("endpoint_gaps", {}))
        # Keep the persisted state schema at the base runner's 1.0 so PR #138 state
        # remains resumable. The strengthened audit semantics are versioned separately.
        state["daily_run_contract_version"] = DAILY_RUN_CONTRACT_VERSION
        state["checkpoint_contract_version"] = CHECKPOINT_CONTRACT_VERSION
        state["run_attempt"] = int(state.get("run_attempt") or 0) + 1
        state["attempt_started_at_utc"] = datetime.now(timezone.utc).isoformat()
        self._save_state(state)
        return state

    def _finish_step(
        self,
        state: dict[str, Any],
        name: str,
        *,
        status: str,
        input_hash: str,
        output: Mapping[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        payload = dict(output or {})
        state.setdefault("steps", {})[name] = {
            "checkpoint_contract_version": CHECKPOINT_CONTRACT_VERSION,
            "status": status,
            "attempt": int(state.get("run_attempt") or 1),
            "input_sha256": input_hash,
            "output_sha256": base._identity(payload),
            "artifact_sha256": _artifact_hashes(payload),
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
            "output": payload,
            "error": error,
        }
        self._save_state(state)

    def _step_reusable(self, state: Mapping[str, Any], name: str, input_hash: str) -> bool:
        record = self._step(state, name)
        if record.get("status") != "SUCCEEDED" or record.get("input_sha256") != input_hash:
            return False
        # PR #138 checkpoints are intentionally re-run once through the audited
        # contract. The inner sync layer reuses its staged artifacts, so this upgrade
        # does not repeat provider downloads.
        if record.get("checkpoint_contract_version") != CHECKPOINT_CONTRACT_VERSION:
            return False
        output = record.get("output", {})
        if not isinstance(output, Mapping):
            raise RuntimeError(f"daily run checkpoint output is invalid: {name}")
        if record.get("output_sha256") != base._identity(output):
            raise RuntimeError(f"daily run checkpoint output hash mismatch: {name}")

        artifacts = record.get("artifact_sha256", {})
        if not isinstance(artifacts, Mapping):
            raise RuntimeError(f"daily run checkpoint artifact manifest is invalid: {name}")
        for raw_path, expected_sha in artifacts.items():
            path = Path(str(raw_path))
            if not path.is_file():
                raise RuntimeError(f"daily run checkpoint artifact is missing: {name}: {path}")
            if sha256_file(path) != expected_sha:
                raise RuntimeError(f"daily run checkpoint artifact hash mismatch: {name}: {path}")

        if name == "dataset_verify":
            data_path = Path(str(output.get("data_path") or ""))
            manifest = data_path / "dataset_manifest.json"
            expected = str(output.get("dataset_manifest_sha256") or "")
            if not manifest.is_file() or not expected or sha256_file(manifest) != expected:
                raise RuntimeError("verified DatasetVersion manifest changed after checkpoint")
        elif name == "feature_materialization":
            feature_root = Path(str(output.get("feature_root") or ""))
            instrument_root = Path(str(output.get("instrument_root") or ""))
            feature_count = (
                sum(1 for path in feature_root.rglob("*") if path.is_file())
                if feature_root.is_dir()
                else 0
            )
            instrument_count = (
                sum(1 for path in instrument_root.rglob("*") if path.is_file())
                if instrument_root.is_dir()
                else 0
            )
            if feature_count != int(output.get("feature_file_count") or -1):
                raise RuntimeError("feature materialization checkpoint no longer matches DatasetVersion")
            if instrument_count != int(output.get("instrument_file_count") or -1):
                raise RuntimeError("instrument materialization checkpoint no longer matches DatasetVersion")
        return True

    def _verify_dataset(self, plan: Mapping[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        dataset = super()._verify_dataset(plan, state)
        data_path = Path(str(dataset["data_path"]))
        feature_root = data_path / "features"
        instrument_root = data_path / "instruments"
        input_hash = base._identity(
            {
                "dataset_version_id": dataset["dataset_version_id"],
                "dataset_manifest_sha256": dataset["dataset_manifest_sha256"],
                "target_session": plan["target_session"],
            },
            prefix="features-",
        )
        if self._step_reusable(state, "feature_materialization", input_hash):
            return dataset

        feature_files = [path for path in feature_root.rglob("*") if path.is_file()]
        instrument_files = [path for path in instrument_root.rglob("*") if path.is_file()]
        if not feature_root.is_dir() or not feature_files:
            raise RuntimeError(f"published DatasetVersion has no materialized Qlib features: {feature_root}")
        if not instrument_root.is_dir() or not instrument_files:
            raise RuntimeError(
                f"published DatasetVersion has no materialized Qlib instruments: {instrument_root}"
            )
        output = {
            "dataset_version_id": dataset["dataset_version_id"],
            "feature_file_count": len(feature_files),
            "instrument_file_count": len(instrument_files),
            "feature_root": str(feature_root),
            "instrument_root": str(instrument_root),
        }
        self._finish_step(
            state,
            "feature_materialization",
            status="SUCCEEDED",
            input_hash=input_hash,
            output=output,
        )
        return dataset

    def _sync_apply_state(self, state: Mapping[str, Any]) -> dict[str, Any]:
        sync = self._step(state, "sync_publish")
        output = sync.get("output", {})
        output = output if isinstance(output, Mapping) else {}
        raw_path = output.get("apply_state")
        if not raw_path:
            return {}
        path = Path(str(raw_path))
        if not path.is_file():
            return {"status": "MISSING", "path": str(path)}
        try:
            return base._read_json(path)
        except (OSError, ValueError, json.JSONDecodeError, TypeError) as exc:
            return {
                "status": "CORRUPT",
                "path": str(path),
                "error": f"{type(exc).__name__}: {exc}",
            }

    def _business_run_id(self, plan: Mapping[str, Any], state: Mapping[str, Any]) -> str:
        dataset = self._step(state, "dataset_verify").get("output", {})
        dataset = dataset if isinstance(dataset, Mapping) else {}
        regression = self._step(state, "regression_backtest")
        regression_output = regression.get("output", {})
        regression_output = regression_output if isinstance(regression_output, Mapping) else {}
        research = self.settings.data.get("research", {})
        research = research if isinstance(research, Mapping) else {}
        universe = self.settings.data.get("universe", {})
        universe = universe if isinstance(universe, Mapping) else {}
        return base._identity(
            {
                "contract": DAILY_RUN_CONTRACT_VERSION,
                "target_session": plan.get("target_session"),
                "mode": plan.get("mode"),
                "config_sha256": state.get("config_sha256") or plan.get("config_sha256"),
                "immutable_input": {
                    "data_release_id": dataset.get("data_release_id"),
                    "dataset_version_id": dataset.get("dataset_version_id"),
                    "dataset_manifest_sha256": dataset.get("dataset_manifest_sha256"),
                },
                "universe": dict(universe),
                "benchmark": research.get("benchmark"),
                "regression": {
                    "enabled": regression_output.get("enabled"),
                    "input_sha256": regression.get("input_sha256"),
                },
            },
            prefix="dailybiz-",
        )

    def _lineage(self, plan: Mapping[str, Any], state: Mapping[str, Any]) -> dict[str, Any]:
        dataset = self._step(state, "dataset_verify").get("output", {})
        dataset = dataset if isinstance(dataset, Mapping) else {}
        sync_state = self._sync_apply_state(state)
        sync_steps = sync_state.get("steps", {})
        sync_steps = sync_steps if isinstance(sync_steps, Mapping) else {}
        freshness = sync_steps.get("freshness_gate", {})
        freshness = freshness if isinstance(freshness, Mapping) else {}
        freshness_output = freshness.get("output", {})
        freshness_output = freshness_output if isinstance(freshness_output, Mapping) else {}
        research = self.settings.data.get("research", {})
        research = research if isinstance(research, Mapping) else {}
        universe = self.settings.data.get("universe", {})
        universe = universe if isinstance(universe, Mapping) else {}
        regression = self._step(state, "regression_backtest")
        regression_output = regression.get("output", {})
        regression_output = regression_output if isinstance(regression_output, Mapping) else {}
        return {
            "target_session": plan.get("target_session"),
            "sync_plan_id": plan.get("plan_id"),
            "config_sha256": state.get("config_sha256") or plan.get("config_sha256"),
            "code": dict(state.get("code") or {}),
            "provider": {
                "watermarks_at_plan": state.get("provider_watermarks", {}),
                "endpoint_gaps_at_plan": state.get("endpoint_gaps", {}),
            },
            "immutable_dataset": {
                "data_release_id": dataset.get("data_release_id"),
                "dataset_version_id": dataset.get("dataset_version_id"),
                "dataset_manifest_sha256": dataset.get("dataset_manifest_sha256"),
            },
            "universe": {
                "configuration": dict(universe),
                "freshness_gate": freshness_output.get("universe"),
            },
            "benchmark": {
                "symbol": research.get("benchmark"),
                "freshness_gate": freshness_output.get("benchmark"),
            },
            "model_policy": {
                "automatic_selection": False,
                "automatic_promotion": False,
                "regression_status": regression.get("status"),
                "regression_input_sha256": regression.get("input_sha256"),
                "regression_output_sha256": regression.get("output_sha256"),
                "command": regression_output.get("command"),
            },
        }

    def _checkpoint_ledger(self, plan: Mapping[str, Any], state: Mapping[str, Any]) -> dict[str, Any]:
        ready, reason = base._session_ready(self.settings, str(plan["target_session"]))
        if plan.get("status") == "SKIPPED_NON_TRADING_DAY":
            preflight_status = "SKIPPED_NON_TRADING_DAY"
        elif not ready:
            preflight_status = "BLOCKED"
        else:
            preflight_status = "SUCCEEDED"
        preflight_output = {
            "target_session": plan.get("target_session"),
            "plan_status": plan.get("status"),
            "session_ready": ready,
            "reason": reason,
        }
        ledger: dict[str, Any] = {
            "calendar_preflight": {
                "status": preflight_status,
                "attempt": int(state.get("run_attempt") or 1),
                "input_sha256": base._identity(
                    {
                        "target_session": plan.get("target_session"),
                        "config_sha256": plan.get("config_sha256"),
                    }
                ),
                "output_sha256": base._identity(preflight_output),
                "artifact_sha256": {},
                "error": reason,
            }
        }
        sync_state = self._sync_apply_state(state)
        sync_steps = sync_state.get("steps", {})
        if isinstance(sync_steps, Mapping):
            for name, record in sync_steps.items():
                if isinstance(record, Mapping):
                    ledger[f"sync.{name}"] = _record_summary(record)
        top_steps = state.get("steps", {})
        if isinstance(top_steps, Mapping):
            for name, record in top_steps.items():
                if isinstance(record, Mapping):
                    ledger[str(name)] = _record_summary(record)
        return ledger

    def _render_report(self, plan: Mapping[str, Any], state: dict[str, Any]) -> Path:
        path = super()._render_report(plan, state)
        lineage = self._lineage(plan, state)
        business_run_id = self._business_run_id(plan, state)
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n## Audit lineage\n\n")
            handle.write(f"- Business run identity: `{business_run_id}`\n")
            handle.write(f"- Config SHA256: `{lineage['config_sha256'] or 'N/A'}`\n")
            handle.write(f"- Git commit: `{lineage['code'].get('git_commit') or 'N/A'}`\n")
            handle.write(
                "- Provider watermarks at plan: `"
                + json.dumps(
                    lineage["provider"]["watermarks_at_plan"],
                    sort_keys=True,
                    default=str,
                )
                + "`\n"
            )
            handle.write(
                "- Universe: `"
                + json.dumps(
                    lineage["universe"]["configuration"],
                    sort_keys=True,
                    default=str,
                )
                + "`\n"
            )
            handle.write(f"- Benchmark: `{lineage['benchmark']['symbol'] or 'N/A'}`\n")
            handle.write("- Automatic model selection/promotion: `false/false`\n")
        return path

    def _enrich_manifest(self, path: Path, plan: Mapping[str, Any]) -> Path:
        payload = base._read_json(path)
        payload["daily_run_contract_version"] = DAILY_RUN_CONTRACT_VERSION
        payload["checkpoint_contract_version"] = CHECKPOINT_CONTRACT_VERSION
        payload["business_run_id"] = self._business_run_id(plan, payload)
        payload["lineage"] = self._lineage(plan, payload)
        payload["checkpoint_ledger"] = self._checkpoint_ledger(plan, payload)
        return base._atomic_json(payload, path)

    def execute_plan(
        self,
        plan_id: str,
        *,
        force_full: bool = False,
        regression: bool | None = None,
        backfill: bool = False,
    ) -> Path:
        plan = self.sync.load_plan(plan_id)
        try:
            path = super().execute_plan(
                plan_id,
                force_full=force_full,
                regression=regression,
                backfill=backfill,
            )
        except Exception:
            failed = self._manifest_path(plan_id)
            if failed.is_file():
                self._enrich_manifest(failed, plan)
            raise
        return self._enrich_manifest(path, plan)

    def backfill(
        self,
        start: str,
        end: str,
        *,
        mode: str,
        force_full: bool,
    ) -> list[Path]:
        """Catch up only sessions after the active immutable dataset coverage.

        Historical revisions inside already-published coverage belong to a current
        `historical-audit` run. Replaying them as a backfill would risk rolling the
        active alias backwards before the monotonic publish guard rejected the plan.
        """

        dates = self.sync._local_open_dates(start, end)
        active = self.sync._active_dataset_manifest()
        if active is not None:
            active_end = self.sync._manifest_end(active[0])
            if active_end is not None:
                blocked = [
                    trade_date
                    for trade_date in dates
                    if pd.Timestamp(trade_date).normalize() <= active_end
                ]
                if blocked:
                    raise ValueError(
                        "backfill may only advance sessions after the active DatasetVersion; "
                        f"active_end={active_end.date()} first_blocked={blocked[0]}. "
                        "Use --mode historical-audit --as-of <current/latest session> "
                        "for revisions inside published coverage."
                    )
        return super().backfill(start, end, mode=mode, force_full=force_full)


def main() -> int:
    args = base.parser().parse_args()
    if args.resume and args.plan:
        raise ValueError("--resume cannot be combined with --plan")
    if args.backfill and (args.resume or args.as_of):
        raise ValueError("--backfill cannot be combined with --resume/--as-of")
    settings = Settings.load(args.config, require_tushare=False, create_dirs=True)
    runner = DailyResearchRun(settings)

    if args.backfill:
        outputs = runner.backfill(
            args.backfill[0],
            args.backfill[1],
            mode=args.mode,
            force_full=args.force_full,
        )
        print(json.dumps({"backfill": [str(path) for path in outputs]}, ensure_ascii=False))
        return 0

    if args.resume:
        path = runner.execute_plan(
            args.resume,
            force_full=args.force_full,
            regression=args.regression,
        )
        print(json.dumps(base._read_json(path), ensure_ascii=False))
        return 0

    plan_path = runner.plan(as_of=args.as_of, mode=args.mode)
    if args.plan:
        print(json.dumps(base._read_json(plan_path), ensure_ascii=False))
        return 0
    plan = base._read_json(plan_path)
    result = runner.execute_plan(
        str(plan["plan_id"]),
        force_full=args.force_full,
        regression=args.regression,
    )
    print(json.dumps(base._read_json(result), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
