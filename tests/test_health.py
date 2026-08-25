from __future__ import annotations

from pathlib import Path

from tushare_qlib.health import dependency_health, live_health, ready_health
from tushare_qlib.settings import Paths, Settings


def _settings(tmp_path: Path) -> Settings:
    paths = Paths.from_root(tmp_path / "data")
    return Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={"mode": "standalone", "data_source": {"kind": "auto"}, "qlib": {}},
        paths=paths,
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=paths.root / "qlib" / "current",
    )


def test_health_remains_ready_when_data_and_platform_are_unavailable(tmp_path: Path):
    settings = _settings(tmp_path)

    assert live_health() == {"status": "live"}
    assert ready_health(settings)["status"] == "ready"
    dependencies = dependency_health(settings)
    assert dependencies["local_data"] == "data_unavailable"
    assert dependencies["platform"] == "not_configured"
    assert dependencies["execution_export"] == "degraded"


def test_ready_health_fails_closed_for_corrupt_registry(tmp_path: Path):
    settings = _settings(tmp_path)
    settings.registry_path.parent.mkdir(parents=True)
    settings.registry_path.write_bytes(b"not a sqlite database")

    result = ready_health(settings)

    assert result["status"] == "not_ready"
    assert result["registry"] == "unavailable"
    assert result["checks"]["registry"] == "database_error"
