from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import pandas as pd

from qlib_platform.data.planned_daily_sync import PlannedDailySyncService
from qlib_platform.datasets.dataset_manifest import verify_dataset_manifest
from qlib_platform.datasets.dataset_resolver import resolve_dataset
from qlib_platform.notifier import FeishuNotifier, NotificationEnvelope
from qlib_platform.runtime.file_lock import FileLock
from qlib_platform.runtime.runtime_resources import resource_argument
from qlib_platform.settings import Settings

RUN_SCHEMA_VERSION = "1.0"


def _atomic_json(payload: Mapping[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path


def _identity(payload: Any, prefix: str = "") -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return prefix + hashlib.sha256(encoded).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise TypeError(f"expected JSON object: {path}")
    return loaded


def _production_config(settings: Settings) -> Mapping[str, Any]:
    production = settings.data.get("production", {})
    if not isinstance(production, Mapping):
        return {}
    daily = production.get("daily_run", {})
    return daily if isinstance(daily, Mapping) else {}


def _session_ready(settings: Settings, target_session: str) -> tuple[bool, str | None]:
    data_sync = settings.data.get("data_sync", {})
    data_sync = data_sync if isinstance(data_sync, Mapping) else {}
    timezone_name = str(data_sync.get("timezone") or "Asia/Shanghai")
    ready_after = str(data_sync.get("ready_after") or "17:30")
    zone = ZoneInfo(timezone_name)
    now = datetime.now(zone)
    target = pd.Timestamp(target_session).date()
    if target < now.date():
        return True, None
    if target > now.date():
        return False, "TARGET_SESSION_IN_FUTURE"
    cutoff = time.fromisoformat(ready_after)
    if now.time() < cutoff:
        return False, f"PROVIDER_WINDOW_NOT_READY_BEFORE_{ready_after}"
    return True, None


class DailyResearchRun:
    """Fail-closed production DAG from local calendar planning to immutable research evidence."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.sync = PlannedDailySyncService(settings)
        self.root = settings.paths.state / "daily_run"

    def _run_root(self, plan_id: str) -> Path:
        return self.root / "runs" / plan_id

    def _state_path(self, plan_id: str) -> Path:
        return self._run_root(plan_id) / "run_state.json"

    def _manifest_path(self, plan_id: str) -> Path:
        return self._run_root(plan_id) / "manifest.json"

    def _report_path(self, plan_id: str) -> Path:
        return self._run_root(plan_id) / "report.md"

    def _load_state(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        path = self._state_path(str(plan["plan_id"]))
        if path.is_file():
            state = _read_json(path)
            if state.get("schema_version") != RUN_SCHEMA_VERSION:
                raise ValueError(f"unsupported daily run state schema: {state.get('schema_version')}")
            return state
        return {
            "schema_version": RUN_SCHEMA_VERSION,
            "run_id": f"daily-{plan['plan_id']}",
            "plan_id": plan["plan_id"],
            "target_session": plan["target_session"],
            "status": "RUNNING",
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "steps": {},
        }

    def _save_state(self, state: Mapping[str, Any]) -> None:
        _atomic_json(state, self._state_path(str(state["plan_id"])))

    @staticmethod
    def _step(state: Mapping[str, Any], name: str) -> Mapping[str, Any]:
        steps = state.get("steps", {})
        if not isinstance(steps, Mapping):
            return {}
        value = steps.get(name, {})
        return value if isinstance(value, Mapping) else {}

    def _step_reusable(self, state: Mapping[str, Any], name: str, input_hash: str) -> bool:
        record = self._step(state, name)
        return record.get("status") == "SUCCEEDED" and record.get("input_sha256") == input_hash

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
        state.setdefault("steps", {})[name] = {
            "status": status,
            "input_sha256": input_hash,
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
            "output": dict(output or {}),
            "error": error,
        }
        self._save_state(state)

    def _block_downstream(self, state: dict[str, Any], after: str, reason: str) -> None:
        order = ["sync_publish", "dataset_verify", "regression_backtest", "report", "notification"]
        try:
            start = order.index(after) + 1
        except ValueError:
            start = 0
        for name in order[start:]:
            if self._step(state, name).get("status") in {"SUCCEEDED", "SKIPPED"}:
                continue
            state.setdefault("steps", {})[name] = {
                "status": "BLOCKED",
                "input_sha256": "",
                "finished_at_utc": datetime.now(timezone.utc).isoformat(),
                "output": {},
                "error": reason,
            }
        self._save_state(state)

    def _verify_dataset(self, plan: Mapping[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        resolved = resolve_dataset(self.settings, allow_legacy=False)
        target = str(plan["target_session"])
        input_hash = _identity(
            {
                "version_id": resolved.version_id,
                "manifest_sha256": resolved.manifest_sha256,
                "target_session": target,
            },
            prefix="verify-",
        )
        if self._step_reusable(state, "dataset_verify", input_hash):
            return dict(self._step(state, "dataset_verify").get("output", {}))

        daily_cfg = _production_config(self.settings)
        verification = daily_cfg.get("verification", {})
        verification = verification if isinstance(verification, Mapping) else {}
        mode = str(verification.get("mode") or "sampled")
        sample_size = int(verification.get("sample_size") or 64)
        workers = int(verification.get("workers") or 2)
        evidence: dict[str, object] = {}
        manifest = verify_dataset_manifest(
            resolved.manifest_path,
            mode=mode,
            sample_size=sample_size,
            workers=workers,
            receipt_dir=self.settings.paths.state / "verification_receipts",
            reuse_receipt=True,
            evidence=evidence,
        )
        calendar_path = resolved.data_path / "calendars" / "day.txt"
        calendar = {
            pd.Timestamp(line.strip()).strftime("%Y%m%d")
            for line in calendar_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        if target not in calendar:
            raise RuntimeError(f"published Qlib dataset does not contain target session {target}")
        semantic = manifest.get("semantic_contract", {})
        semantic = semantic if isinstance(semantic, Mapping) else {}
        output = {
            "dataset_reference": resolved.reference,
            "dataset_version_id": resolved.version_id,
            "dataset_manifest_sha256": resolved.manifest_sha256,
            "data_release_id": manifest.get("data_release_id") or semantic.get("data_release_id"),
            "data_path": str(resolved.data_path),
            "verification": evidence,
        }
        self._finish_step(
            state,
            "dataset_verify",
            status="SUCCEEDED",
            input_hash=input_hash,
            output=output,
        )
        return output

    def _regression_enabled(self, override: bool | None) -> bool:
        if override is not None:
            return override
        cfg = _production_config(self.settings).get("regression", {})
        cfg = cfg if isinstance(cfg, Mapping) else {}
        return bool(cfg.get("enabled", False))

    def _run_regression(
        self,
        plan: Mapping[str, Any],
        state: dict[str, Any],
        dataset: Mapping[str, Any],
        *,
        enabled: bool,
    ) -> dict[str, Any]:
        version_id = str(dataset["dataset_version_id"])
        run_root = self._run_root(str(plan["plan_id"])) / "regression"
        command = [
            sys.executable,
            "-m",
            "qlib_platform.research.workflow.governed_quickstart",
            "--config",
            str(self.settings.config_path),
            "baseline",
            "--dataset-ref",
            version_id,
            "--verify-mode",
            "manifest",
            "--workers",
            "1",
            "--artifact-level",
            "minimal",
            "--output",
            str(run_root),
        ]
        input_hash = _identity(
            {
                "dataset_version_id": version_id,
                "data_release_id": dataset.get("data_release_id"),
                "target_session": plan["target_session"],
                "command": command[3:],
            },
            prefix="regression-",
        )
        if self._step_reusable(state, "regression_backtest", input_hash):
            return dict(self._step(state, "regression_backtest").get("output", {}))
        if not enabled:
            output = {
                "enabled": False,
                "reason": "frozen regression is not enabled in production.daily_run.regression",
            }
            self._finish_step(
                state,
                "regression_backtest",
                status="SKIPPED",
                input_hash=input_hash,
                output=output,
            )
            return output

        run_root.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
        stdout_path = run_root / "stdout.log"
        stderr_path = run_root / "stderr.log"
        stdout_path.write_text(completed.stdout, encoding="utf-8")
        stderr_path.write_text(completed.stderr, encoding="utf-8")
        output = {
            "enabled": True,
            "exit_code": completed.returncode,
            "command": command,
            "stdout": str(stdout_path),
            "stderr": str(stderr_path),
            "output_root": str(run_root),
        }
        if completed.returncode != 0:
            self._finish_step(
                state,
                "regression_backtest",
                status="FAILED",
                input_hash=input_hash,
                output=output,
                error=f"frozen baseline regression exited {completed.returncode}",
            )
            raise RuntimeError(f"frozen baseline regression exited {completed.returncode}")
        self._finish_step(
            state,
            "regression_backtest",
            status="SUCCEEDED",
            input_hash=input_hash,
            output=output,
        )
        return output

    def _render_report(self, plan: Mapping[str, Any], state: dict[str, Any]) -> Path:
        path = self._report_path(str(plan["plan_id"]))
        rows = []
        for name, record in state.get("steps", {}).items():
            rows.append(f"| `{name}` | {record.get('status')} | {record.get('error') or ''} |")
        dataset = self._step(state, "dataset_verify").get("output", {})
        report = "\n".join(
            [
                f"# Daily Research Run — {plan['target_session']}",
                "",
                f"- Plan: `{plan['plan_id']}`",
                f"- Run status: `{state.get('status', 'RUNNING')}`",
                f"- DatasetVersion: `{dataset.get('dataset_version_id', 'N/A')}`",
                f"- DataRelease: `{dataset.get('data_release_id', 'N/A')}`",
                f"- Dataset manifest: `{dataset.get('dataset_manifest_sha256', 'N/A')}`",
                "",
                "## Stages",
                "",
                "| Stage | Status | Error |",
                "|---|---|---|",
                *rows,
                "",
                "This report is evidence only; it does not authorize model promotion or live execution.",
                "",
            ]
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".md.tmp")
        temporary.write_text(report, encoding="utf-8")
        os.replace(temporary, path)
        return path

    def _notify(self, plan: Mapping[str, Any], state: dict[str, Any], report: Path) -> dict[str, Any]:
        cfg = _production_config(self.settings)
        notify_enabled = bool(cfg.get("notify", True))
        url = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
        input_hash = _identity(
            {
                "plan_id": plan["plan_id"],
                "status": state.get("status"),
                "report": str(report),
            },
            prefix="notify-",
        )
        if not notify_enabled or not url:
            output = {"sent": False, "reason": "disabled" if not notify_enabled else "webhook-not-configured"}
            self._finish_step(
                state,
                "notification",
                status="SKIPPED",
                input_hash=input_hash,
                output=output,
            )
            return output
        dataset = self._step(state, "dataset_verify").get("output", {})
        envelope = NotificationEnvelope(
            message_id=f"daily-research-{plan['plan_id']}",
            message_kind="DAILY_RESEARCH_RUN",
            business_date=str(plan["target_session"]),
            trade_date=str(plan["target_session"]),
            channel="feishu",
            title=f"Daily Research {state.get('status')} | {plan['target_session']}",
            summary=(
                f"plan={plan['plan_id']}\n"
                f"dataset={dataset.get('dataset_version_id', 'N/A')}\n"
                f"release={dataset.get('data_release_id', 'N/A')}"
            ),
            sections={
                "Stages": {name: record.get("status") for name, record in state.get("steps", {}).items()},
                "Report": str(report),
            },
        )
        notifier = FeishuNotifier(
            url,
            secret=os.getenv("FEISHU_WEBHOOK_SECRET", "").strip() or None,
        )
        notifier.send(envelope)
        output = {"sent": True, "message_id": envelope.message_id}
        self._finish_step(
            state,
            "notification",
            status="SUCCEEDED",
            input_hash=input_hash,
            output=output,
        )
        return output

    def execute_plan(
        self,
        plan_id: str,
        *,
        force_full: bool = False,
        regression: bool | None = None,
        backfill: bool = False,
    ) -> Path:
        plan = self.sync.load_plan(plan_id)
        state = self._load_state(plan)
        run_root = self._run_root(plan_id)
        run_root.mkdir(parents=True, exist_ok=True)
        if plan.get("status") == "SKIPPED_NON_TRADING_DAY":
            state["status"] = "SKIPPED_NON_TRADING_DAY"
            state["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
            self._save_state(state)
            report = self._render_report(plan, state)
            manifest = {
                **state,
                "plan": str(self.sync._plan_path(plan_id)),
                "report": str(report),
            }
            return _atomic_json(manifest, self._manifest_path(plan_id))

        ready, reason = _session_ready(self.settings, str(plan["target_session"]))
        if not ready:
            state["status"] = "BLOCKED"
            state["block_reason"] = reason
            state["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
            self._block_downstream(state, "calendar_preflight", str(reason))
            report = self._render_report(plan, state)
            return _atomic_json(
                {**state, "plan": str(self.sync._plan_path(plan_id)), "report": str(report)},
                self._manifest_path(plan_id),
            )

        lease_path = self.root / "leases" / f"{plan['target_session']}.lock"
        with FileLock(
            lease_path,
            blocking=False,
            unavailable_message=f"daily research run already owns session {plan['target_session']}",
        ):
            lease_meta = lease_path.with_suffix(".json")
            _atomic_json(
                {
                    "plan_id": plan_id,
                    "target_session": plan["target_session"],
                    "pid": os.getpid(),
                    "acquired_at_utc": datetime.now(timezone.utc).isoformat(),
                },
                lease_meta,
            )
            try:
                sync_input = _identity(
                    {
                        "plan_id": plan_id,
                        "config_sha256": plan["config_sha256"],
                        "force_full": force_full,
                    },
                    prefix="sync-",
                )
                if not self._step_reusable(state, "sync_publish", sync_input):
                    try:
                        apply_state = self.sync.apply_plan(plan_id, force_full=force_full)
                    except Exception as exc:
                        self._finish_step(
                            state,
                            "sync_publish",
                            status="FAILED",
                            input_hash=sync_input,
                            error=f"{type(exc).__name__}: {exc}",
                        )
                        self._block_downstream(state, "sync_publish", str(exc))
                        raise
                    self._finish_step(
                        state,
                        "sync_publish",
                        status="SUCCEEDED",
                        input_hash=sync_input,
                        output={"apply_state": str(apply_state)},
                    )

                try:
                    dataset = self._verify_dataset(plan, state)
                except Exception as exc:
                    verify_input = _identity({"plan_id": plan_id}, prefix="verify-failed-")
                    self._finish_step(
                        state,
                        "dataset_verify",
                        status="FAILED",
                        input_hash=verify_input,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    self._block_downstream(state, "dataset_verify", str(exc))
                    raise

                regression_enabled = False if backfill else self._regression_enabled(regression)
                self._run_regression(
                    plan,
                    state,
                    dataset,
                    enabled=regression_enabled,
                )
                state["status"] = "SUCCEEDED"
                state["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
                self._save_state(state)
                report = self._render_report(plan, state)
                report_input = _identity({"state": state, "report_path": str(report)}, prefix="report-")
                self._finish_step(
                    state,
                    "report",
                    status="SUCCEEDED",
                    input_hash=report_input,
                    output={"path": str(report)},
                )
                self._notify(plan, state, report)
                state["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
                self._save_state(state)
                manifest = {
                    **state,
                    "plan": str(self.sync._plan_path(plan_id)),
                    "report": str(report),
                    "dataset": dataset,
                    "immutable_input": {
                        "data_release_id": dataset.get("data_release_id"),
                        "dataset_version_id": dataset.get("dataset_version_id"),
                        "dataset_manifest_sha256": dataset.get("dataset_manifest_sha256"),
                    },
                    "backfill": backfill,
                }
                return _atomic_json(manifest, self._manifest_path(plan_id))
            except Exception:
                state["status"] = "FAILED"
                state["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
                self._save_state(state)
                report = self._render_report(plan, state)
                _atomic_json(
                    {**state, "plan": str(self.sync._plan_path(plan_id)), "report": str(report)},
                    self._manifest_path(plan_id),
                )
                raise
            finally:
                _atomic_json(
                    {
                        "plan_id": plan_id,
                        "target_session": plan["target_session"],
                        "released_at_utc": datetime.now(timezone.utc).isoformat(),
                    },
                    lease_meta,
                )

    def plan(self, *, as_of: str | None, mode: str) -> Path:
        return self.sync.create_plan(as_of=as_of, mode=mode)

    def run(
        self,
        *,
        as_of: str | None,
        mode: str,
        force_full: bool,
        regression: bool | None,
    ) -> Path:
        plan_path = self.plan(as_of=as_of, mode=mode)
        plan = _read_json(plan_path)
        return self.execute_plan(
            str(plan["plan_id"]),
            force_full=force_full,
            regression=regression,
        )

    def backfill(
        self,
        start: str,
        end: str,
        *,
        mode: str,
        force_full: bool,
    ) -> list[Path]:
        dates = self.sync._local_open_dates(start, end)
        outputs: list[Path] = []
        for trade_date in dates:
            plan_path = self.plan(as_of=trade_date, mode=mode)
            plan = _read_json(plan_path)
            outputs.append(
                self.execute_plan(
                    str(plan["plan_id"]),
                    force_full=force_full,
                    regression=False,
                    backfill=True,
                )
            )
        return outputs


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Fail-closed daily research DAG: plan -> sync -> immutable dataset -> regression -> report"
    )
    p.add_argument("--config", default=resource_argument("configs/pipeline.standalone.yaml"))
    p.add_argument("--as-of", help="target exchange session (YYYY-MM-DD or YYYYMMDD)")
    p.add_argument("--mode", choices=["routine", "historical-audit"], default="routine")
    p.add_argument("--plan", action="store_true", help="write a network-free local delta plan and exit")
    p.add_argument("--resume", metavar="PLAN_ID", help="resume an existing durable plan")
    p.add_argument("--backfill", nargs=2, metavar=("START", "END"))
    p.add_argument("--force-full", action="store_true")
    p.add_argument(
        "--regression",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="override production.daily_run.regression.enabled",
    )
    return p


def main() -> int:
    args = parser().parse_args()
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
        print(json.dumps(_read_json(path), ensure_ascii=False))
        return 0

    plan_path = runner.plan(as_of=args.as_of, mode=args.mode)
    if args.plan:
        print(json.dumps(_read_json(plan_path), ensure_ascii=False))
        return 0
    plan = _read_json(plan_path)
    result = runner.execute_plan(
        str(plan["plan_id"]),
        force_full=args.force_full,
        regression=args.regression,
    )
    print(json.dumps(_read_json(result), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
