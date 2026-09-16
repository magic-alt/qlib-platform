from __future__ import annotations

from pathlib import Path

import yaml

from qlib_platform.qlib_compat.capabilities import (
    check_capabilities,
    load_capability_manifest,
    materialize_capability_matrix,
)


def test_pinned_capability_manifest_declares_auditable_compatibility_policy() -> None:
    manifest = load_capability_manifest()

    assert manifest["schema_version"] == 2
    assert manifest["contract"] == "qlib-upstream-compatibility-v2"
    assert manifest["qlib_version"] == "0.9.7"
    assert manifest["policy"]["native_passthrough"] is True
    assert manifest["policy"]["monkey_patch_upstream"] is False
    assert manifest["policy"]["full_compatibility_claim_requires_matrix_gate"] is True
    assert manifest["certification_gate"]["full_compatibility_threshold"] == 1.0

    capabilities = {item["id"]: item for item in materialize_capability_matrix(manifest)}
    for capability_id in (
        "core.object_factory",
        "workflow.qrun",
        "workflow.task_train",
        "workflow.recorder",
        "workflow.signal_record",
        "workflow.signal_analysis_record",
        "workflow.portfolio_analysis_record",
        "data.dataset_h",
        "data.alpha158",
        "model.lightgbm",
        "strategy.topk_dropout",
        "backtest.exchange",
    ):
        capability = capabilities[capability_id]
        assert capability["certification"] == "certified"
        assert capability["evidence"]["positive"]
        assert capability["owner"] == "qlib-compat"
        assert capability["version"] == "0.9.7"
        assert capability["os"] == ["ubuntu-latest"]
        assert capability["python"] == ["3.11"]

    for capability in capabilities.values():
        assert capability["certification"] in {
            "certified",
            "upstream-native",
            "not-certified",
            "unsupported",
        }
        assert capability["owner"]
        assert capability["version"]
        assert capability["os"]
        assert capability["python"]
        if capability["negativeRequired"]:
            assert capability["evidence"]["negative"]

    assert capabilities["model.linear"]["extra"] == "sklearn"
    assert capabilities["model.pytorch_hist"]["extra"] == "pytorch"
    assert capabilities["tuner.hyperopt"]["extra"] == "tuner"
    assert capabilities["report.graph"]["extra"] == "analysis"

    rl = capabilities["reinforcement_learning.order_execution"]
    assert rl["certification"] == "unsupported"
    assert rl["extra"] == "upstream-rl-legacy"
    assert "tianshou<=0.4.10" in rl["knownDeviation"]

    exceptions = {item["id"]: item for item in manifest["known_upstream_exceptions"]}
    rl_exception = exceptions["qlib.rl.order_execution.dependencies"]
    assert rl_exception["status"] == "blocked_upstream_security"
    assert rl_exception["upstream_requirement"] == "tianshou<=0.4.10"
    assert rl_exception["transitive_constraint"] == "protobuf~=3.19.0"


def test_required_core_capabilities_are_importable_for_pinned_qlib() -> None:
    report = check_capabilities()

    assert report["expectedQlibVersion"] == "0.9.7"
    assert report["passed"], report["requiredFailures"]
    assert report["certificationErrors"] == []
    assert report["knownUpstreamExceptions"]
    assert report["certificationSummary"]["certifiedCapabilities"] > 0
    assert report["certificationSummary"]["fullCompatibilityClaimEligible"] is False

    results = {item["id"]: item for item in report["results"]}
    assert results["workflow.qrun"]["status"] == "CERTIFIED"
    assert results["data.alpha158"]["status"] == "CERTIFIED"
    assert results["core.qlib"]["status"] == "AVAILABLE"
    assert results["reinforcement_learning.order_execution"]["status"] == "UNSUPPORTED"


def test_optional_dependency_absence_is_unavailable_not_pass(tmp_path: Path) -> None:
    manifest = {
        "schema_version": 1,
        "contract": "test",
        "qlib_version": "0.9.7",
        "known_upstream_exceptions": [],
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
    assert optional_report["results"][0]["status"] == "UNAVAILABLE"
    assert optional_report["results"][0]["available"] is False
    assert not required_report["passed"]
    assert required_report["results"][0]["status"] == "FAIL"
    assert required_report["requiredFailures"] == ["missing.optional"]


def test_certified_capability_without_evidence_fails_closed() -> None:
    manifest = {
        "schema_version": 1,
        "contract": "test",
        "qlib_version": "0.9.7",
        "known_upstream_exceptions": [],
        "capabilities": [
            {
                "id": "invalid.certified",
                "target": "qlib",
                "level": "required",
                "certification": "certified",
            }
        ],
    }

    report = check_capabilities(manifest)

    assert not report["passed"]
    assert report["certificationErrors"] == [
        "invalid.certified: certified capability has no positive evidence"
    ]


def test_unsupported_capability_is_explicit_even_when_import_is_unavailable() -> None:
    manifest = {
        "schema_version": 1,
        "contract": "test",
        "qlib_version": "0.9.7",
        "known_upstream_exceptions": [],
        "capabilities": [
            {
                "id": "legacy.unsupported",
                "target": "module_that_must_not_exist_for_qlib_platform_test",
                "level": "optional",
                "certification": "unsupported",
                "known_deviation": "legacy dependency chain is intentionally excluded",
            }
        ],
    }

    report = check_capabilities(manifest)

    assert report["passed"]
    assert report["results"][0]["status"] == "UNSUPPORTED"
    assert report["results"][0]["certification"] == "unsupported"
