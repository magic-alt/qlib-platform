from __future__ import annotations

import json
from pathlib import Path

import pytest

from qlib_platform.settings import Settings


def test_production_policy_matches_v1_golden(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("QLIB_PROD_ROOT", str(tmp_path / "prod"))
    monkeypatch.setenv("TUSHARE_TOKEN", "GOLDEN_SECRET_SENTINEL")
    report = Settings.load("configs/pipeline_tushare_prod.yaml", create_dirs=False).production_policy_report()
    golden = json.loads(Path("tests/golden/production_policy_v1.json").read_text(encoding="utf-8"))

    stable = {
        key: report[key]
        for key in (
            "schemaVersion",
            "configSchemaVersion",
            "environment",
            "status",
            "passed",
            "secrets",
            "retry",
            "release",
            "coverage",
            "retention",
            "timezones",
            "configMigration",
        )
    }
    assert stable == golden
    assert "GOLDEN_SECRET_SENTINEL" not in json.dumps(report, ensure_ascii=False)


def test_dev_and_ci_profiles_do_not_require_production_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("QLIB_PROD_ROOT", raising=False)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setenv("QLIB_REPO", str(tmp_path / "qlib-repo"))
    monkeypatch.setenv("QLIB_DATA_URI", str(tmp_path / "qlib-data"))

    dev = Settings.load("configs/pipeline_tushare_dev.yaml", create_dirs=False)
    ci = Settings.load("configs/pipeline_tushare_ci.yaml", create_dirs=False)

    assert dev.environment == "dev"
    assert ci.environment == "ci"
    assert dev.production_policy_report()["status"] == "NOT_APPLICABLE"
    assert ci.production_policy_report()["status"] == "NOT_APPLICABLE"
