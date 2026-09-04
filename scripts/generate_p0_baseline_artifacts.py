"""Add P0 audit, signal-quality and cost-stress evidence to a completed run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from qlib_platform.backtest_report import write_backtest_report
from qlib_platform.p0_baseline import write_p0_artifacts
from qlib_platform.settings import Paths, Settings


def _settings_for_data_root(data_root: Path) -> Settings:
    root = data_root.expanduser().resolve()
    return Settings(
        config_path=Path.cwd() / "configs" / "pipeline.yaml",
        data={"project_root": str(root)},
        paths=Paths.from_root(root),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=root,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir")
    parser.add_argument(
        "--allow-reconciliation-failure",
        action="store_true",
        help="write evidence but do not fail the process; use only to diagnose legacy runs",
    )
    parser.add_argument(
        "--data-root",
        default=Path("data"),
        type=Path,
        help="project data root used to regenerate the child report",
    )
    args = parser.parse_args()
    result = write_p0_artifacts(args.run_dir, strict_reconciliation=False)
    write_backtest_report(_settings_for_data_root(args.data_root), args.run_dir)
    if not args.allow_reconciliation_failure and not result["auditReconciliation"]["passed"]:
        raise RuntimeError("AUDIT_RECONCILIATION_FAILED")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
