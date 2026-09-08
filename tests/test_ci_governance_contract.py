from __future__ import annotations

from pathlib import Path

import yaml


def _workflow(path: str) -> dict:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_main_ci_uses_85_percent_target_with_forward_coverage_ratchet() -> None:
    workflow = _workflow(".github/workflows/ci.yml")
    quality = workflow["jobs"]["quality"]
    rendered = "\n".join(str(step.get("run", "")) for step in quality["steps"])

    assert "scripts/run_comprehensive_checks.py" in rendered
    assert "--coverage-threshold 85" in rendered
    assert "--cov-fail-under=60" not in rendered

    runner = Path("scripts/run_comprehensive_checks.py").read_text(encoding="utf-8")
    assert '"--coverage-floor"' in runner
    assert "default=77.9" in runner
    assert '"--diff-coverage-threshold"' in runner
    assert "default=85.0" in runner
    assert "_github_diff_base" in runner
    assert "_diff_coverage" in runner


def test_main_ci_exposes_stable_required_status_context() -> None:
    workflow = _workflow(".github/workflows/ci.yml")
    required = workflow["jobs"]["required-ci"]

    assert required["name"] == "required-ci"
    assert required["if"] == "always()"
    assert set(required["needs"]) == {
        "change-scope",
        "governance",
        "test-matrix",
        "quality",
        "standalone-isolation",
        "clean-machine-wheel",
        "clean-machine-lightgbm",
        "dnn-bundle-parity",
    }


def test_qlib_contract_has_real_e2e_and_stable_required_status_context() -> None:
    workflow = _workflow(".github/workflows/qlib-capability.yml")
    jobs = workflow["jobs"]

    real = jobs["qlib-real-alpha158-conformance"]
    rendered = "\n".join(str(step.get("run", "")) for step in real["steps"])
    assert real["env"]["QLIB_REAL_CONFORMANCE"] == "1"
    assert "tests/test_qlib_real_conformance.py" in rendered

    required = jobs["qlib-required"]
    assert required["name"] == "qlib-required"
    assert required["if"] == "always()"
    assert "qlib-real-alpha158-conformance" in required["needs"]
