from __future__ import annotations

from pathlib import Path

import yaml

from qlib_platform.qlib_compat.capabilities import check_capabilities, load_capability_manifest


def test_pinned_capability_manifest_declares_additive_superset_policy() -> None:
    manifest = load_capability_manifest()

    assert manifest["contract"] == "qlib-native-superset-v1"
    assert manifest["qlib_version"] == "0.9.7"
    assert manifest["policy"] == {
        "native_passthrough": True,
        "platform_allowlist_required": False,
        "monkey_patch_upstream": False,
        "certified_platform_lane_is_opt_in": True,
    }

    capabilities = {item["id"]: item for item in manifest["capabilities"]}
    for capability_id in (
        "core.object_factory",
        "workflow.qrun",
        "workflow.task_train",
        "workflow.recorder",
        "data.dataset_h",
        "data.handler_lp",
        "model.base",
        "strategy.base",
        "backtest.executor",
        "backtest.exchange",
    ):
        assert capabilities[capability_id]["level"] == "required"

    assert capabilities["model.linear"]["extra"] == "sklearn"
    assert capabilities["model.pytorch_hist"]["extra"] == "pytorch"
    assert capabilities["reinforcement_learning"]["level"] == "required"
    assert capabilities["reinforcement_learning.order_execution"]["extra"] == "rl"
    assert capabilities["tuner.hyperopt"]["extra"] == "tuner"
    assert capabilities["report.graph"]["extra"] == "analysis"


def test_required_core_capabilities_are_importable_for_pinned_qlib() -> None:
    report = check_capabilities()

    assert report["expectedQlibVersion"] == "0.9.7"
    assert report["passed"], report["requiredFailures"]


def test_required_extra_promotes_optional_capabilities_to_fail_closed(tmp_path: Path) -> None:
    manifest = {
        "schema_version": 1,
        "contract": "test",
        "qlib_version": "0.9.7",
        "capabilities": [
            {
                "id": "missing.optional",
                "target": "module_that_must_not_exist_for_qlib_platform_test",
                "level": "optional",
                "extra": "pytorch",
            }
        ],
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")

    optional_report = check_capabilities(load_capability_manifest(path))
    required_report = check_capabilities(load_capability_manifest(path), require_extras=("pytorch",))

    assert optional_report["passed"]
    assert not required_report["passed"]
    assert required_report["requiredFailures"] == ["missing.optional"]
