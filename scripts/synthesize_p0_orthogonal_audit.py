"""Write a fail-closed audit receipt for an orthogonal P0 synthesis."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from qlib_platform.research.workflow.baseline import write_orthogonal_synthesis_receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child-run-dir", action="append", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = write_orthogonal_synthesis_receipt(args.output, args.child_run_dir)
    print(json.dumps({"auditReceipt": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
