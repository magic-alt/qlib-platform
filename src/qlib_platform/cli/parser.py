from __future__ import annotations

import argparse

from qlib_platform.runtime.runtime_resources import resource_argument
from qlib_platform.cli.commands import backtesting, data, datasets, feedback, ops, releases, research, runtime

COMMAND_REGISTRARS = (
    backtesting.register,
    data.register,
    datasets.register,
    feedback.register,
    ops.register,
    releases.register,
    research.register,
    runtime.register,
)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Auditable platform DataRelease -> Qlib research pipeline")
    root.add_argument("--config", default=resource_argument("configs/pipeline.standalone.yaml"))
    sub = root.add_subparsers(dest="command", required=True)
    for register in COMMAND_REGISTRARS:
        register(sub)
    return root
