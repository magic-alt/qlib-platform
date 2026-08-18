"""Add P0 audit, signal-quality and cost-stress evidence to a completed run."""

from __future__ import annotations

import argparse
import json

from tushare_qlib.p0_baseline import write_p0_artifacts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir")
    parser.add_argument(
        "--allow-reconciliation-failure",
        action="store_true",
        help="write evidence but do not fail the process; use only to diagnose legacy runs",
    )
    args = parser.parse_args()
    result = write_p0_artifacts(args.run_dir, strict_reconciliation=not args.allow_reconciliation_failure)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
