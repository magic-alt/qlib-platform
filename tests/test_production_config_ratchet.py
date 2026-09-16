from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

import pandas as pd
import pytest
import yaml

from qlib_platform.cli.entrypoint import production_plan
from qlib_platform.data.certified_daily_sync import CertifiedDailySyncService
from qlib_platform.datasets.dataset_registry import DatasetRegistry
from qlib_platform.production_policy import ProductionPolicyError
from qlib_platform.settings import Settings


def _valid_prod_payload() -> dict[str, Any]:
    endpoint_policy = {
        "max_staleness_sessions": 0,
        "min_rows": 2,
        "min_previous_session_ratio": 0.90,
    }
    return {
        "config_schema_version": "1.0",
        "environment": "prod",
        "mode": "standalone",
        "project_root": ".",
        "project_root_env": "QLIB_PROD_ROOT",
        "storage": {"registry_path": ""},
        "data_source": {
            "kind": "tushare",
            "tushare": {"token_env": "TUSHARE_TOKEN", "calls_per_minute": 180},
            "runtime": {
                "max_attempts": 4,
                "base_sleep_seconds": 1.0,
                "max_sleep_seconds": 30.0,
                "jitter_ratio": 0.1,
            },
            "optional_endpoints": {
                "daily": True,
                "adj_factor": True,
                "daily_basic": True,
            },
        },
        "release_store": {
            "kind": "file",
            "root": "",
            "publish_on_sync": True,
            "immutable": True,
        },
        "qlib": {
            "repo_path": "",
            "dataset_dir": "",
            "versions_root": "",
            "dataset_name": "prod",
            "dataset_ref": "production-current",
        },
        "research": {"allow_dirty_research": False},
        "data_sync": {"timezone": "Asia/Shanghai"},
        "production": {"daily_run": {"schedule": {"timezone": "Asia/Shanghai"}}},
        "production_policy": {
            "schema_version": "1.0",
            "paths": {"artifacts": "artifacts", "cache": "cache", "logs": "logs"},
            "release": {
                "validate_before_activation": True,
                "atomic_active_pointer": True,
            },
            "coverage": {
                "required_endpoints": {
                    "daily": dict(endpoint_policy),
                    "adj_factor": dict(endpoint_policy),
                    "daily_basic": dict(endpoint_policy),
                }
            },
            "retention": {
                "bronze_days": 30,
                "silver_days": 30,
                "releases_days": 90,
                "artifacts_days": 30,
                "logs_days": 14,
                "release_min_count": 3,
                "protect_referenced_objects": True,
            },
            "config_migration": {
                "unknown_version": "fail",
                "defaults_change": "explicit_migration",
                "rollback": "previous_config_and_active_release",
            },
        },
    }


def _write_config(tmp_path: Path, payload: dict[str, Any]) -> Path:
    path = tmp_path / "pipeline_prod.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _set_prod_env(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setenv("QLIB_PROD_ROOT", str(root))
    monkeypatch.setenv("TUSHARE_TOKEN", "TOP_SECRET_SENTINEL")


def test_repository_prod_profile_resolves_isolated_paths_without_secret_leak(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "prod-root"
    _set_prod_env(monkeypatch, root)

    settings = Settings.load("configs/pipeline_tushare_prod.yaml", create_dirs=False)
    report = settings.production_policy_report()
    serialized = json.dumps(report, ensure_ascii=False)

    assert settings.environment == "prod"
    assert report["status"] == "ENFORCED"
    assert report["secrets"] == {
        "tushareTokenRef": "TUSHARE_TOKEN",
        "inlineSecretsAllowed": False,
    }
    assert "TOP_SECRET_SENTINEL" not in serialized
    for key in (
        "registry",
        "releaseStore",
        "qlibDataset",
        "qlibVersions",
        "stateRoot",
        "qualityRoot",
        "outputRoot",
        "artifactRoot",
        "cacheRoot",
        "logRoot",
    ):
        Path(report["paths"][key]).relative_to(root.resolve())
    assert not root.exists()


def test_plan_entrypoint_is_zero_write_and_redacts_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "prod-root"
    _set_prod_env(monkeypatch, root)

    payload = production_plan("configs/pipeline_tushare_prod.yaml")

    assert payload["writeMode"] == "NONE"
    assert payload["environment"] == "prod"
    assert "TOP_SECRET_SENTINEL" not in json.dumps(payload, ensure_ascii=False)
    assert not root.exists()


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (("offline_on_empty", True), "offline_on_empty"),
        (("failure_policy", "test_coverage_mode"), "failure_policy"),
        (("migration_mode", "in_place_rewrite"), "migration_mode"),
        (("skip_required_quality", True), "skip_required_quality"),
    ],
)
def test_unsafe_prod_flags_fail_before_directory_creation(
    mutation: tuple[str, object],
    match: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "prod-root"
    _set_prod_env(monkeypatch, root)
    payload = _valid_prod_payload()
    payload[mutation[0]] = mutation[1]
    config = _write_config(tmp_path, payload)

    with pytest.raises(ProductionPolicyError, match=match):
        Settings.load(config, create_dirs=True)

    assert not root.exists()


def test_prod_rejects_disabled_required_endpoint_before_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "prod-root"
    _set_prod_env(monkeypatch, root)
    payload = _valid_prod_payload()
    payload["data_source"]["optional_endpoints"]["adj_factor"] = False
    config = _write_config(tmp_path, payload)

    with pytest.raises(ProductionPolicyError, match="adj_factor"):
        Settings.load(config, create_dirs=True)

    assert not root.exists()


def test_prod_rejects_inline_secret_without_echoing_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "prod-root"
    _set_prod_env(monkeypatch, root)
    payload = _valid_prod_payload()
    payload["data_source"]["tushare"]["token"] = "INLINE_SECRET_SENTINEL"
    config = _write_config(tmp_path, payload)

    with pytest.raises(ProductionPolicyError) as excinfo:
        Settings.load(config, create_dirs=True)

    assert "data_source.tushare.token" in str(excinfo.value)
    assert "INLINE_SECRET_SENTINEL" not in str(excinfo.value)
    assert not root.exists()


def test_prod_requires_secret_reference_to_resolve_before_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "prod-root"
    monkeypatch.setenv("QLIB_PROD_ROOT", str(root))
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    config = _write_config(tmp_path, _valid_prod_payload())

    with pytest.raises(ProductionPolicyError, match="TUSHARE_TOKEN"):
        Settings.load(config, create_dirs=True)

    assert not root.exists()


@pytest.mark.parametrize(
    ("mutator", "match"),
    [
        (lambda payload: payload.__setitem__("config_schema_version", "2.0"), "config_schema_version"),
        (
            lambda payload: payload["production_policy"]["paths"].__setitem__(
                "logs", "../escaped-logs"
            ),
            "paths.logs",
        ),
        (
            lambda payload: payload["release_store"].__setitem__("immutable", False),
            "immutable",
        ),
        (
            lambda payload: payload["production_policy"]["retention"].__setitem__(
                "releases_days", 0
            ),
            "releases_days",
        ),
        (
            lambda payload: payload["production"]["daily_run"]["schedule"].__setitem__(
                "timezone", ""
            ),
            "schedule.timezone",
        ),
        (
            lambda payload: payload["data_source"]["runtime"].__setitem__("max_attempts", 1),
            "max_attempts",
        ),
    ],
)
def test_prod_policy_ratchets_schema_paths_release_retention_timezone_and_retry(
    mutator: Any,
    match: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "prod-root"
    _set_prod_env(monkeypatch, root)
    payload = _valid_prod_payload()
    mutator(payload)
    config = _write_config(tmp_path, payload)

    with pytest.raises(ProductionPolicyError, match=match):
        Settings.load(config, create_dirs=True)

    assert not root.exists()


class _CoverageStore:
    def __init__(self, frames: dict[tuple[str, str], pd.DataFrame]) -> None:
        self.frames = frames

    def list_dates(self, dataset: str) -> list[str]:
        return sorted(date for name, date in self.frames if name == dataset)

    def read(self, dataset: str, date: str) -> pd.DataFrame:
        return self.frames[(dataset, date)]


def test_required_endpoint_gate_rejects_partial_daily_basic(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "prod-root"
    _set_prod_env(monkeypatch, root)
    settings = Settings.load(_write_config(tmp_path, _valid_prod_payload()), create_dirs=False)
    previous = "20260914"
    target = "20260915"
    frames: dict[tuple[str, str], pd.DataFrame] = {}
    for endpoint in ("daily", "adj_factor", "daily_basic"):
        frames[(endpoint, previous)] = pd.DataFrame({"ts_code": ["A", "B", "C", "D"]})
        frames[(endpoint, target)] = pd.DataFrame({"ts_code": ["A", "B", "C", "D"]})
    frames[("daily_basic", target)] = pd.DataFrame({"ts_code": ["A", "B"]})

    service = object.__new__(CertifiedDailySyncService)
    service.settings = settings
    service.store = _CoverageStore(frames)

    result = service._required_endpoint_policy_gate(target)

    assert result["daily"]["fresh"] is True
    assert result["adj_factor"]["fresh"] is True
    assert result["daily_basic"]["coverage_ratio"] == 0.5
    assert result["daily_basic"]["fresh"] is False


def _dataset_manifest(path: Path, *, version_id: str, release_id: str) -> dict[str, Any]:
    path.write_text("{}", encoding="utf-8")
    return {
        "schema_version": "3.0",
        "version_id": version_id,
        "dataset_name": "cn_tushare_prod",
        "layer": "qlib",
        "status": "VALIDATED",
        "data_path": str(path.parent / version_id),
        "data_release_id": release_id,
        "partitions": [],
    }


def _release(release_id: str, manifest_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        data_release_id=release_id,
        profile="cn_tushare_prod",
        manifest_path=manifest_path,
        manifest_sha256=f"sha-{release_id}",
        coverage={"start": "20260101", "end": "20260915"},
        manifest={"lineage": {"producer": "test"}},
    )


def test_atomic_snapshot_promotion_rolls_back_aliases_on_mid_transaction_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    registry = DatasetRegistry(tmp_path / "registry" / "qlib.sqlite")
    old_release = "ds_old"
    new_release = "ds_new"
    old_manifest = tmp_path / "old_dataset_manifest.json"
    new_manifest = tmp_path / "new_dataset_manifest.json"
    old_release_manifest = tmp_path / "old_release.json"
    new_release_manifest = tmp_path / "new_release.json"

    registry.register_release(_release(old_release, old_release_manifest))
    registry.register_dataset(
        _dataset_manifest(old_manifest, version_id="dv_old", release_id=old_release),
        old_manifest,
    )
    registry.promote_research_snapshot(
        release_alias="production-current",
        data_release_id=old_release,
        dataset_alias="production-current",
        dataset_version_id="dv_old",
    )
    registry.register_release(_release(new_release, new_release_manifest))
    registry.register_dataset(
        _dataset_manifest(new_manifest, version_id="dv_new", release_id=new_release),
        new_manifest,
    )

    original_connect = registry.connect

    class _FailingConnection:
        def __init__(self, connection: Any) -> None:
            self.connection = connection

        def execute(self, sql: str, parameters: object = ()) -> Any:
            if "INSERT INTO release_aliases" in sql:
                raise RuntimeError("simulated active-pointer failure")
            return self.connection.execute(sql, parameters)

        def executescript(self, sql: str) -> Any:
            return self.connection.executescript(sql)

    @contextmanager
    def failing_connect() -> Iterator[_FailingConnection]:
        with original_connect() as connection:
            yield _FailingConnection(connection)

    monkeypatch.setattr(registry, "connect", failing_connect)

    with pytest.raises(RuntimeError, match="active-pointer failure"):
        registry.promote_research_snapshot(
            release_alias="production-current",
            data_release_id=new_release,
            dataset_alias="production-current",
            dataset_version_id="dv_new",
        )

    assert registry.resolve_release_alias("production-current") == old_release
    assert registry.resolve("production-current").version_id == "dv_old"
    assert registry.get_version("dv_new").status == "VALIDATED"
