from __future__ import annotations

import argparse
import json
from pathlib import Path

from qlib_platform.qlib_compat.capabilities import check_capabilities, load_capability_manifest
from qlib_platform.qlib_compat.workflow import run_qrun


def _parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="tq-qlib",
        description="Native Microsoft Qlib compatibility lane for qlib-platform",
    )
    sub = root.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run an upstream qrun workflow without platform rewriting")
    run.add_argument("workflow")
    run.add_argument("--experiment-name", default="workflow")
    run.add_argument("--uri-folder", default="mlruns")

    check = sub.add_parser("capability-check", help="verify the pinned Qlib capability contract")
    check.add_argument("--manifest")
    check.add_argument("--require-extra", action="append", default=[])
    check.add_argument("--output")
    return root


def main() -> None:
    args = _parser().parse_args()
    if args.command == "run":
        run_qrun(
            args.workflow,
            experiment_name=args.experiment_name,
            uri_folder=args.uri_folder,
        )
        return

    manifest = load_capability_manifest(args.manifest)
    report = check_capabilities(manifest, require_extras=args.require_extra)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if not report["passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
