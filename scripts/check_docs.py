from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from qlib_platform.docs_check import check_documentation


def main() -> int:
    parser = argparse.ArgumentParser(description="Check qlib-platform documentation invariants")
    parser.add_argument("--root", default=".")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    findings = check_documentation(Path(args.root))
    if args.json:
        print(json.dumps([asdict(item) for item in findings], ensure_ascii=False, indent=2))
    else:
        for item in findings:
            print(f"{item.rule_id} {item.severity} {item.path}:{item.line} {item.message}")
        print(f"documentation_findings={len(findings)}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
