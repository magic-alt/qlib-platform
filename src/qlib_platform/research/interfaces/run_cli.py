from __future__ import annotations

import argparse
import json
from typing import Sequence

from qlib_platform.research.evidence.run_manifest import inspect_run, reproduce_run
from qlib_platform.runtime.runtime_resources import resource_argument
from qlib_platform.settings import Settings


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="qlib-platform")
    root.add_argument("--config", default=resource_argument("configs/pipeline.standalone.yaml"))
    run = root.add_subparsers(dest="command", required=True).add_parser(
        "run", help="inspect or deterministically reproduce an archived research run"
    )
    sub = run.add_subparsers(dest="run_command", required=True)
    inspect = sub.add_parser("inspect", help="inspect immutable run lineage and verify its proof chain")
    inspect.add_argument("run_id")
    reproduce = sub.add_parser("reproduce", help="verify or execute a frozen run reproduction contract")
    reproduce.add_argument("run_id")
    mode = reproduce.add_mutually_exclusive_group(required=True)
    mode.add_argument("--verify-only", action="store_true")
    mode.add_argument("--execute", action="store_true")
    return root


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(list(argv) if argv is not None else None)
    settings = Settings.load(args.config, require_tushare=False, create_dirs=False)
    if args.run_command == "inspect":
        result = inspect_run(settings, args.run_id)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if result["verification"]["passed"] else 2

    result = reproduce_run(settings, args.run_id, execute=bool(args.execute))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if not result["verification"]["passed"] or not result["runtime_context"]["passed"]:
        return 2
    execution = result.get("execution")
    if isinstance(execution, dict) and execution.get("status") != "EXECUTED":
        return 2
    return 0
