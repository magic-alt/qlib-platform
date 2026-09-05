from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

from qlib_platform.qlib_compat import QlibObjectSpec, init_qlib_object
from qlib_platform.qlib_compat.workflow import run_qrun, task_train_native


class _ArbitraryResearchObject:
    def __init__(self, *, value: int, label: str = "custom") -> None:
        self.value = value
        self.label = label


def test_object_spec_round_trip_preserves_native_qlib_shape() -> None:
    spec = QlibObjectSpec.from_mapping(
        {"class": "SomethingNewUpstream", "module_path": "future_qlib.module", "kwargs": {"depth": 7}}
    )
    assert spec.to_config() == {
        "class": "SomethingNewUpstream",
        "module_path": "future_qlib.module",
        "kwargs": {"depth": 7},
    }


def test_generic_factory_accepts_arbitrary_importable_class_without_registry() -> None:
    module_name = "_qlib_platform_test_custom_model"
    module = types.ModuleType(module_name)
    module.ArbitraryResearchObject = _ArbitraryResearchObject
    sys.modules[module_name] = module
    try:
        instance = init_qlib_object(
            QlibObjectSpec(
                class_name="ArbitraryResearchObject",
                module_path=module_name,
                kwargs={"value": 42},
            )
        )
    finally:
        sys.modules.pop(module_name, None)

    assert isinstance(instance, _ArbitraryResearchObject)
    assert instance.value == 42
    assert instance.label == "custom"


def test_native_qrun_delegates_to_upstream_without_rewriting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import qlib.cli.run

    workflow = tmp_path / "workflow.yaml"
    workflow.write_text("task: {model: custom}\n", encoding="utf-8")
    calls: list[tuple[str, str, str]] = []

    def fake_workflow(
        config_path: str, experiment_name: str = "workflow", uri_folder: str = "mlruns"
    ) -> None:
        calls.append((config_path, experiment_name, uri_folder))

    original = workflow.read_bytes()
    monkeypatch.setattr(qlib.cli.run, "workflow", fake_workflow)
    run_qrun(workflow, experiment_name="native-exp", uri_folder="native-runs")

    assert calls == [(str(workflow), "native-exp", "native-runs")]
    assert workflow.read_bytes() == original


def test_native_task_train_delegates_exact_task(monkeypatch: pytest.MonkeyPatch) -> None:
    import qlib.model.trainer

    captured: dict[str, Any] = {}
    recorder = object()

    def fake_task_train(task: dict[str, Any], experiment_name: str = "workflow") -> object:
        captured["task"] = task
        captured["experiment_name"] = experiment_name
        return recorder

    monkeypatch.setattr(qlib.model.trainer, "task_train", fake_task_train)
    task = {
        "model": {"class": "FutureModel", "module_path": "my.models"},
        "dataset": {"class": "FutureDataset", "module_path": "my.datasets"},
    }
    result = task_train_native(task, experiment_name="native-task")

    assert result is recorder
    assert captured == {"task": task, "experiment_name": "native-task"}


def test_importing_compat_layer_does_not_monkey_patch_qlib() -> None:
    from qlib.utils import init_instance_by_config as before
    from qlib_platform.qlib_compat.factory import init_qlib_object as _  # noqa: F401
    from qlib.utils import init_instance_by_config as after

    assert after is before
