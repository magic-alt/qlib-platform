from __future__ import annotations

from pathlib import Path

from qlib_platform.data.sources.registry import resolve_data_source_name
from qlib_platform.settings import Paths, Settings


def _settings(tmp_path: Path, mysql: dict[str, object]) -> Settings:
    paths = Paths.from_root(tmp_path / "data")
    paths.mkdirs()
    return Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={
            "data_source": {"kind": "auto", "mysql": mysql},
            "qlib": {"dataset_name": "test", "dataset_version": "test"},
        },
        paths=paths,
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=paths.root / "qlib" / "test",
    )


def test_auto_ignores_inherited_empty_mysql_mapping(tmp_path: Path, monkeypatch):
    for name in ("MYSQL_HOST", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE"):
        monkeypatch.delenv(name, raising=False)
    settings = _settings(
        tmp_path,
        {
            "host": "",
            "user": "",
            "password": "",
            "database": "",
            "schema": "lean_canonical_v1",
        },
    )

    assert resolve_data_source_name(settings) == "tushare"


def test_auto_selects_mysql_only_when_connection_contract_is_complete(tmp_path: Path):
    settings = _settings(
        tmp_path,
        {
            "host": "127.0.0.1",
            "user": "research",
            "password": "secret",
            "database": "market",
            "schema": "lean_canonical_v1",
        },
    )

    assert resolve_data_source_name(settings) == "mysql"
