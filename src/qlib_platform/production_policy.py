from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

CONFIG_SCHEMA_VERSION = "1.0"
PRODUCTION_POLICY_SCHEMA_VERSION = "1.0"
ENVIRONMENTS = {"dev", "ci", "prod", "benchmark"}
_REQUIRED_ENDPOINTS = ("daily", "adj_factor", "daily_basic")
_UNSAFE_TRUE_KEYS = {
    "test_coverage_mode",
    "in_place_rewrite",
    "offline_on_empty",
    "skip_required_quality",
    "bypass_required_quality",
    "allow_missing_required",
    "allow_incomplete_required",
}
_SECRET_VALUE_KEYS = {
    "token",
    "password",
    "secret",
    "api_key",
    "apikey",
    "dsn",
    "webhook_url",
}


class ProductionPolicyError(ValueError):
    """Raised before any filesystem mutation when a prod profile is unsafe."""


def _mapping(value: object, path: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProductionPolicyError(f"production policy requires mapping: {path}")
    return dict(value)


def _walk(value: object, prefix: str = "") -> list[tuple[str, str, object]]:
    rows: list[tuple[str, str, object]] = []
    if not isinstance(value, Mapping):
        return rows
    for raw_key, child in value.items():
        key = str(raw_key)
        path = f"{prefix}.{key}" if prefix else key
        rows.append((path, key.lower(), child))
        rows.extend(_walk(child, path))
    return rows


def environment_name(config: Mapping[str, Any]) -> str:
    value = str(config.get("environment") or "").strip().lower()
    if value and value not in ENVIRONMENTS:
        raise ProductionPolicyError(
            f"environment must be one of {sorted(ENVIRONMENTS)}; got {value!r}"
        )
    return value


def _unsafe_flag_violations(config: Mapping[str, Any]) -> list[str]:
    violations: list[str] = []
    for path, key, value in _walk(config):
        if key in _UNSAFE_TRUE_KEYS and bool(value):
            violations.append(f"{path} is forbidden in production")
        if key == "failure_policy" and str(value).strip().lower() == "test_coverage_mode":
            violations.append(f"{path}=test_coverage_mode is forbidden in production")
        if key == "migration_mode" and str(value).strip().lower() in {
            "in_place_rewrite",
            "in-place-rewrite",
            "mutable",
            "rewrite",
        }:
            violations.append(f"{path} enables in-place mutation")
    return violations


def _secret_violations(config: Mapping[str, Any]) -> list[str]:
    violations: list[str] = []
    for path, key, value in _walk(config):
        if key.endswith("_env") or key.endswith("_secret_ref") or key.endswith("_secret_name"):
            continue
        if key in _SECRET_VALUE_KEYS and value not in {None, ""}:
            violations.append(f"{path} must use an env/secret reference instead of an inline value")
    return violations


def _path_violations(config: Mapping[str, Any], project_root: Path) -> list[str]:
    violations: list[str] = []
    root_env = str(config.get("project_root_env") or "").strip()
    if not root_env:
        violations.append("project_root_env is required for production environment isolation")
    elif not os.getenv(root_env, "").strip():
        violations.append(f"production project root environment variable is not set: {root_env}")
    if not project_root.is_absolute():
        violations.append("resolved production project_root must be absolute")

    checks = (
        ("storage.registry_path", config.get("storage", {}), "registry_path"),
        ("release_store.root", config.get("release_store", {}), "root"),
        ("qlib.dataset_dir", config.get("qlib", {}), "dataset_dir"),
        ("qlib.versions_root", config.get("qlib", {}), "versions_root"),
    )
    for label, owner, key in checks:
        if not isinstance(owner, Mapping):
            violations.append(f"{label.rsplit('.', 1)[0]} must be a mapping")
            continue
        raw = str(owner.get(key) or "").strip()
        if raw and not Path(raw).expanduser().is_absolute():
            violations.append(f"{label} must be empty (derive from project_root) or absolute in prod")
    return violations


def _retry_violations(config: Mapping[str, Any]) -> list[str]:
    source = _mapping(config.get("data_source", {}), "data_source")
    runtime = _mapping(source.get("runtime", {}), "data_source.runtime")
    attempts = int(runtime.get("max_attempts") or 0)
    base_sleep = float(runtime.get("base_sleep_seconds") or 0)
    max_sleep = float(runtime.get("max_sleep_seconds") or 0)
    jitter = float(runtime.get("jitter_ratio") or 0)
    violations: list[str] = []
    if attempts < 2:
        violations.append("data_source.runtime.max_attempts must be >= 2 in prod")
    if base_sleep <= 0 or max_sleep < base_sleep:
        violations.append("prod retry backoff requires 0 < base_sleep_seconds <= max_sleep_seconds")
    if not 0 <= jitter <= 1:
        violations.append("data_source.runtime.jitter_ratio must be between 0 and 1")
    return violations


def _coverage_policy(config: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    policy = _mapping(config.get("production_policy", {}), "production_policy")
    coverage = _mapping(policy.get("coverage", {}), "production_policy.coverage")
    endpoints = _mapping(
        coverage.get("required_endpoints", {}),
        "production_policy.coverage.required_endpoints",
    )
    result: dict[str, dict[str, Any]] = {}
    for endpoint in _REQUIRED_ENDPOINTS:
        item = _mapping(endpoints.get(endpoint, {}), f"production_policy.coverage.required_endpoints.{endpoint}")
        result[endpoint] = item
    return result


def _coverage_violations(config: Mapping[str, Any]) -> list[str]:
    violations: list[str] = []
    for endpoint, policy in _coverage_policy(config).items():
        min_rows = int(policy.get("min_rows") or 0)
        max_staleness = int(policy.get("max_staleness_sessions") or -1)
        ratio = float(policy.get("min_previous_session_ratio") or 0)
        if min_rows < 1:
            violations.append(f"{endpoint} production coverage min_rows must be positive")
        if max_staleness != 0:
            violations.append(f"{endpoint} max_staleness_sessions must be 0 for daily prod publication")
        if not 0 < ratio <= 1:
            violations.append(f"{endpoint} min_previous_session_ratio must be in (0, 1]")
    return violations


def _release_violations(config: Mapping[str, Any]) -> list[str]:
    store = _mapping(config.get("release_store", {}), "release_store")
    policy = _mapping(config.get("production_policy", {}), "production_policy")
    release = _mapping(policy.get("release", {}), "production_policy.release")
    violations: list[str] = []
    if not bool(store.get("publish_on_sync", False)):
        violations.append("release_store.publish_on_sync must be true in prod")
    if not bool(store.get("immutable", False)):
        violations.append("release_store.immutable must be true in prod")
    if not bool(release.get("validate_before_activation", False)):
        violations.append("production release policy must validate before activation")
    if not bool(release.get("atomic_active_pointer", False)):
        violations.append("production release policy must atomically update the active pointer")
    return violations


def _retention_violations(config: Mapping[str, Any]) -> list[str]:
    policy = _mapping(config.get("production_policy", {}), "production_policy")
    retention = _mapping(policy.get("retention", {}), "production_policy.retention")
    violations: list[str] = []
    if retention.get("protect_referenced_objects") is not True:
        violations.append("production retention must protect run-manifest referenced objects")
    for key in ("bronze_days", "silver_days", "artifacts_days", "logs_days", "release_min_count"):
        value = retention.get(key)
        if not isinstance(value, int) or value < 1:
            violations.append(f"production retention {key} must be a positive integer")
    return violations


def _timezone_violations(config: Mapping[str, Any]) -> list[str]:
    data_sync = _mapping(config.get("data_sync", {}), "data_sync")
    production = _mapping(config.get("production", {}), "production")
    daily_run = _mapping(production.get("daily_run", {}), "production.daily_run")
    schedule = _mapping(daily_run.get("schedule", {}), "production.daily_run.schedule")
    violations: list[str] = []
    if not str(data_sync.get("timezone") or "").strip():
        violations.append("data_sync.timezone (market timezone) is required in prod")
    if not str(schedule.get("timezone") or "").strip():
        violations.append("production.daily_run.schedule.timezone is required in prod")
    return violations


def _migration_violations(config: Mapping[str, Any]) -> list[str]:
    schema_version = str(config.get("config_schema_version") or "").strip()
    policy = _mapping(config.get("production_policy", {}), "production_policy")
    policy_version = str(policy.get("schema_version") or "").strip()
    migration = _mapping(policy.get("config_migration", {}), "production_policy.config_migration")
    violations: list[str] = []
    if schema_version != CONFIG_SCHEMA_VERSION:
        violations.append(
            f"unsupported production config_schema_version {schema_version or 'missing'}; expected {CONFIG_SCHEMA_VERSION}"
        )
    if policy_version != PRODUCTION_POLICY_SCHEMA_VERSION:
        violations.append(
            "unsupported production_policy.schema_version "
            f"{policy_version or 'missing'}; expected {PRODUCTION_POLICY_SCHEMA_VERSION}"
        )
    if str(migration.get("unknown_version") or "").strip().lower() != "fail":
        violations.append("production config migration must fail on unknown versions")
    if str(migration.get("defaults_change") or "").strip().lower() != "explicit_migration":
        violations.append("production config default changes require explicit_migration")
    if not str(migration.get("rollback") or "").strip():
        violations.append("production config migration requires an explicit rollback policy")
    return violations


def validate_production_policy(config: Mapping[str, Any], *, project_root: Path) -> dict[str, Any]:
    environment = environment_name(config)
    if environment != "prod":
        return {
            "schemaVersion": PRODUCTION_POLICY_SCHEMA_VERSION,
            "environment": environment or "unspecified",
            "status": "NOT_APPLICABLE",
            "passed": True,
        }

    violations: list[str] = []
    violations.extend(_unsafe_flag_violations(config))
    violations.extend(_secret_violations(config))
    violations.extend(_path_violations(config, project_root))

    source = _mapping(config.get("data_source", {}), "data_source")
    if str(source.get("kind") or "").strip().lower() != "tushare":
        violations.append("prod data_source.kind must be tushare")
    tushare = _mapping(source.get("tushare", {}), "data_source.tushare")
    token_env = str(tushare.get("token_env") or "").strip()
    if not token_env:
        violations.append("data_source.tushare.token_env is required in prod")

    violations.extend(_retry_violations(config))
    violations.extend(_coverage_violations(config))
    violations.extend(_release_violations(config))
    violations.extend(_retention_violations(config))
    violations.extend(_timezone_violations(config))
    violations.extend(_migration_violations(config))

    research = _mapping(config.get("research", {}), "research")
    if bool(research.get("allow_dirty_research", False)):
        violations.append("research.allow_dirty_research must be false in prod")

    if violations:
        joined = "; ".join(dict.fromkeys(violations))
        raise ProductionPolicyError(f"production config preflight rejected: {joined}")

    storage = _mapping(config.get("storage", {}), "storage")
    release_store = _mapping(config.get("release_store", {}), "release_store")
    qlib = _mapping(config.get("qlib", {}), "qlib")
    registry_raw = str(storage.get("registry_path") or "").strip()
    release_raw = str(release_store.get("root") or "").strip()
    dataset_raw = str(qlib.get("dataset_dir") or "").strip()
    versions_raw = str(qlib.get("versions_root") or "").strip()
    policy = _mapping(config.get("production_policy", {}), "production_policy")
    data_sync = _mapping(config.get("data_sync", {}), "data_sync")
    production = _mapping(config.get("production", {}), "production")
    daily_run = _mapping(production.get("daily_run", {}), "production.daily_run")
    schedule = _mapping(daily_run.get("schedule", {}), "production.daily_run.schedule")

    resolved_paths = {
        "projectRoot": str(project_root),
        "registry": str(Path(registry_raw).resolve()) if registry_raw else str(project_root / "registry" / "qlib.sqlite"),
        "releaseStore": str(Path(release_raw).resolve()) if release_raw else str(project_root / "releases"),
        "qlibDataset": str(Path(dataset_raw).resolve()) if dataset_raw else str(project_root / "qlib" / "current"),
        "qlibVersions": str(Path(versions_raw).resolve()) if versions_raw else str(project_root / "qlib" / "versions"),
        "stateRoot": str(project_root / "state"),
        "qualityRoot": str(project_root / "quality"),
        "outputRoot": str(project_root / "output"),
    }
    return {
        "schemaVersion": PRODUCTION_POLICY_SCHEMA_VERSION,
        "configSchemaVersion": CONFIG_SCHEMA_VERSION,
        "environment": environment,
        "status": "ENFORCED",
        "passed": True,
        "paths": resolved_paths,
        "secrets": {
            "tushareTokenRef": token_env,
            "inlineSecretsAllowed": False,
        },
        "retry": dict(_mapping(source.get("runtime", {}), "data_source.runtime")),
        "release": {
            "immutable": True,
            "publishOnSync": True,
            **dict(_mapping(policy.get("release", {}), "production_policy.release")),
        },
        "coverage": _coverage_policy(config),
        "retention": dict(_mapping(policy.get("retention", {}), "production_policy.retention")),
        "timezones": {
            "market": str(data_sync["timezone"]),
            "scheduler": str(schedule["timezone"]),
        },
        "configMigration": dict(
            _mapping(policy.get("config_migration", {}), "production_policy.config_migration")
        ),
        "unsafeChecks": sorted(_UNSAFE_TRUE_KEYS | {"failure_policy=test_coverage_mode", "migration_mode"}),
    }


def required_endpoint_coverage(config: Mapping[str, Any], endpoint: str) -> dict[str, Any]:
    if environment_name(config) != "prod":
        return {}
    return dict(_coverage_policy(config).get(endpoint, {}))
