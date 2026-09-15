from __future__ import annotations

import json
import os
import subprocess
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Mapping

from qlib_platform.data.certified_daily_sync import CertifiedDailySyncService
from qlib_platform.runtime import daily_research_run as base
from qlib_platform.settings import Settings


def _code_provenance(settings: Settings) -> dict[str, str | None]:
    try:
        package_version = version("qlib-platform")
    except PackageNotFoundError:
        package_version = "source-checkout"

    commit = (
        os.getenv("QLIB_PLATFORM_GIT_SHA", "").strip()
        or os.getenv("GITHUB_SHA", "").strip()
        or None
    )
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


class DailyResearchRun(base.DailyResearchRun):
    """Daily DAG bound to the metadata-only planner and certified freshness gates."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.sync = CertifiedDailySyncService(settings)
        self.root = settings.paths.state / "daily_run"

    def _load_state(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        state = super()._load_state(plan)
        state.setdefault("code", _code_provenance(self.settings))
        state.setdefault("config_path", str(self.settings.config_path))
        state.setdefault("config_sha256", str(plan.get("config_sha256") or ""))
        state.setdefault("provider_watermarks", plan.get("watermarks", {}))
        state.setdefault("endpoint_gaps", plan.get("endpoint_gaps", {}))
        self._save_state(state)
        return state

    def _verify_dataset(
        self, plan: Mapping[str, Any], state: dict[str, Any]
    ) -> dict[str, Any]:
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
            raise RuntimeError(
                f"published DatasetVersion has no materialized Qlib features: {feature_root}"
            )
        if not instrument_root.is_dir() or not instrument_files:
            raise RuntimeError(
                f"published DatasetVersion has no materialized Qlib instruments: {instrument_root}"
            )
        output = {
            "dataset_version_id": dataset["dataset_version_id"],
            "feature_file_count": len(feature_files),
            "instrument_file_count": len(instrument_files),
            "feature_root": str(feature_root),
        }
        self._finish_step(
            state,
            "feature_materialization",
            status="SUCCEEDED",
            input_hash=input_hash,
            output=output,
        )
        return dataset


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
