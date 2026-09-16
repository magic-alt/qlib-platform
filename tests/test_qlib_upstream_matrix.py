from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

from qlib_platform.qlib_compat import cli
from qlib_platform.qlib_compat.capabilities import (
    check_capabilities,
    load_capability_manifest,
    materialize_capability_matrix,
)


def _schema2_manifest(capability: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 2,
        "contract": "test",
        "qlib_version": "0.9.7",
        "matrix_defaults": {
            "certification": "upstream-native",
            "owner": "test-owner",
            "version": "0.9.7",
            "os": ["ubuntu-latest"],
            "python": ["3.11"],
            "evidence": {"positive": [], "negative": []},
        },
        "known_upstream_exceptions": [],
        "capabilities": [capability],
    }


def test_allow_version_drift_reports_canary_drift_without_false_runtime_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import qlib

    manifest = {
        "schema_version": 1,
        "contract": "canary-test",
        "qlib_version": "0.9.7",
        "known_upstream_exceptions": [],
        "capabilities": [{"id": "core.qlib", "target": "qlib", "level": "required"}],
    }
    monkeypatch.setattr(qlib, "__version__", "9.9.9")

    strict = check_capabilities(manifest)
    canary = check_capabilities(manifest, allow_version_drift=True)

    assert strict["versionPassed"] is False
    assert strict["passed"] is False
    assert canary["passed"] is True
    assert canary["driftDetected"] is True
    assert canary["versionDetail"] == "expected 0.9.7, got 9.9.9"
    assert canary["results"][0]["status"] == "AVAILABLE"


def test_full_compatibility_claim_requires_every_certifiable_surface_to_be_certified() -> None:
    manifest = {
        "schema_version": 1,
        "contract": "claim-test",
        "qlib_version": "0.9.7",
        "certification_gate": {"full_compatibility_threshold": 1.0},
        "known_upstream_exceptions": [],
        "capabilities": [
            {
                "id": "core.qlib",
                "target": "qlib",
                "level": "required",
                "certification": "certified",
                "evidence": {"positive": ["tests/test_claim.py::test_claim"]},
            }
        ],
    }

    report = check_capabilities(manifest)

    assert report["passed"] is True
    assert report["certificationSummary"]["certifiedCoverage"] == 1.0
    assert report["certificationSummary"]["fullCompatibilityClaimEligible"] is True
    assert report["results"][0]["status"] == "CERTIFIED"


def test_not_certified_surface_is_available_without_becoming_certified() -> None:
    manifest = {
        "schema_version": 1,
        "contract": "not-certified-test",
        "qlib_version": "0.9.7",
        "known_upstream_exceptions": [],
        "capabilities": [
            {
                "id": "core.qlib",
                "target": "qlib",
                "level": "required",
                "certification": "not-certified",
            }
        ],
    }

    report = check_capabilities(manifest)

    assert report["passed"] is True
    assert report["results"][0]["status"] == "NOT_CERTIFIED"
    assert report["certificationSummary"]["fullCompatibilityClaimEligible"] is False


def test_schema2_requires_materialized_owner_version_and_environment_matrix() -> None:
    manifest = _schema2_manifest({"id": "core.qlib", "target": "qlib", "level": "required"})
    defaults = manifest["matrix_defaults"]
    assert isinstance(defaults, dict)
    defaults["os"] = []

    with pytest.raises(ValueError, match="requires owner/version/os/python matrix metadata"):
        materialize_capability_matrix(manifest)


def test_matrix_rejects_invalid_certification_and_evidence_shapes() -> None:
    invalid_status = _schema2_manifest(
        {
            "id": "core.qlib",
            "target": "qlib",
            "level": "required",
            "certification": "magic",
        }
    )
    with pytest.raises(ValueError, match="invalid certification status"):
        materialize_capability_matrix(invalid_status)

    invalid_evidence = _schema2_manifest(
        {
            "id": "core.qlib",
            "target": "qlib",
            "level": "required",
            "evidence": "not-a-mapping",
        }
    )
    with pytest.raises(ValueError, match="evidence must be a mapping"):
        materialize_capability_matrix(invalid_evidence)


def test_matrix_rejects_invalid_defaults_and_sequence_metadata() -> None:
    invalid_defaults = _schema2_manifest(
        {"id": "core.qlib", "target": "qlib", "level": "required"}
    )
    invalid_defaults["matrix_defaults"] = "not-a-mapping"
    with pytest.raises(ValueError, match="matrix_defaults must be a mapping"):
        materialize_capability_matrix(invalid_defaults)

    invalid_sequence = _schema2_manifest(
        {
            "id": "core.qlib",
            "target": "qlib",
            "level": "required",
            "python": "3.11",
        }
    )
    with pytest.raises(ValueError, match="core.qlib.python must be a sequence"):
        materialize_capability_matrix(invalid_sequence)


def test_unsupported_surface_requires_a_documented_deviation() -> None:
    manifest = {
        "schema_version": 1,
        "contract": "unsupported-test",
        "qlib_version": "0.9.7",
        "known_upstream_exceptions": [],
        "capabilities": [
            {
                "id": "legacy.unsupported",
                "target": "qlib",
                "level": "optional",
                "certification": "unsupported",
            }
        ],
    }

    report = check_capabilities(manifest)

    assert report["passed"] is False
    assert report["certificationErrors"] == [
        "legacy.unsupported: unsupported capability requires known_deviation"
    ]


def test_invalid_certification_gate_fails_validation() -> None:
    manifest = {
        "schema_version": 1,
        "contract": "gate-test",
        "qlib_version": "0.9.7",
        "certification_gate": "not-a-mapping",
        "known_upstream_exceptions": [],
        "capabilities": [{"id": "core.qlib", "target": "qlib", "level": "required"}],
    }

    with pytest.raises(ValueError, match="certification_gate must be a mapping"):
        check_capabilities(manifest)


def test_manifest_loader_rejects_unknown_schema(tmp_path: Path) -> None:
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump({"schema_version": 99}), encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported Qlib capability manifest schema"):
        load_capability_manifest(path)


def test_matrix_cli_writes_fully_materialized_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "matrix.json"
    monkeypatch.setattr(sys, "argv", ["tq-qlib", "matrix", "--output", str(output)])

    cli.main()

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["contract"] == "qlib-upstream-compatibility-v2"
    assert payload["qlibVersion"] == "0.9.7"
    assert any(item["id"] == "workflow.qrun" for item in payload["capabilities"])
    assert "qlib-upstream-compatibility-v2" in capsys.readouterr().out
