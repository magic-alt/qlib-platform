from __future__ import annotations

import argparse
import json
import sys

from qlib_platform.runtime.runtime_resources import resource_argument
from qlib_platform.settings import Settings


def _plan_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qlib-platform",
        description="Resolve and validate an environment-specific platform configuration without writes.",
    )
    parser.add_argument("--config", default=resource_argument("configs/pipeline.standalone.yaml"))
    parser.add_argument("--plan", action="store_true", required=True)
    return parser


def production_plan(config: str) -> dict[str, object]:
    settings = Settings.load(config, create_dirs=False)
    return {
        "config": str(settings.config_path),
        "environment": settings.environment,
        "writeMode": "NONE",
        "productionPolicy": settings.production_policy_report(),
    }


def main() -> None:
    argv = sys.argv[1:]
    if "run" in argv:
        from qlib_platform.research.interfaces.run_cli import main as run_main

        code = run_main(argv)
        if code:
            raise SystemExit(code)
        return
    if "--plan" not in argv:
        from qlib_platform.cli.main import main as legacy_main

        legacy_main()
        return

    args = _plan_parser().parse_args(argv)
    print(json.dumps(production_plan(args.config), ensure_ascii=False, indent=2))
