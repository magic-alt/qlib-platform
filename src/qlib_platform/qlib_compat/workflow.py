from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any


def run_qrun(
    config_path: str | Path,
    *,
    experiment_name: str = "workflow",
    uri_folder: str = "mlruns",
) -> None:
    """Run an upstream Qlib workflow with upstream ``qrun`` semantics.

    The platform deliberately delegates to ``qlib.cli.run.workflow``. No task,
    model, dataset, strategy, executor, recorder, or qlib_init field is rewritten.
    """

    from qlib.cli.run import workflow

    workflow(str(Path(config_path)), experiment_name=experiment_name, uri_folder=uri_folder)


def task_train_native(task: Mapping[str, Any], *, experiment_name: str = "workflow") -> Any:
    """Delegate a task to Qlib's native trainer and return its Recorder."""

    from qlib.model.trainer import task_train

    return task_train(dict(task), experiment_name=experiment_name)


def get_qlib_recorder(*, experiment_name: str, recorder_id: str) -> Any:
    """Retrieve a Qlib Recorder without replacing Qlib's experiment manager."""

    from qlib.workflow import R

    return R.get_recorder(experiment_name=experiment_name, recorder_id=recorder_id)
