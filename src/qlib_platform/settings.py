from __future__ import annotations

import os
import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from qlib_platform.runtime.runtime_resources import resource_path

_ENV_PATTERN = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}")


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        expanded = os.path.expandvars(value)
        unresolved = _ENV_PATTERN.findall(expanded)
        if unresolved:
            raise RuntimeError(f"Unresolved environment variables: {', '.join(unresolved)}")
        return expanded
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    return value


def _require_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a mapping")
    return value


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _load_config(path: Path, seen: frozenset[Path] = frozenset()) -> dict[str, Any]:
    source = path.expanduser().resolve()
    if source in seen:
        raise ValueError(f"configuration extends cycle: {source}")
    with source.open("r", encoding="utf-8") as fp:
        loaded = _require_mapping(yaml.safe_load(fp), "root config")
    parent = loaded.pop("extends", None)
    if parent is None:
        return loaded
    parent_path = Path(str(parent)).expanduser()
    if not parent_path.is_absolute():
        parent_path = source.parent / parent_path
    return _deep_merge(_load_config(parent_path, seen | {source}), loaded)


def _configured_env_override(config: dict[str, Any], key: str) -> str:
    """Read an environment override only when the selected profile opts in.

    This mirrors ``project_root_env``. It prevents process-global variables (for
    example CI's QLIB_DATA_URI) from silently changing unrelated custom configs,
    while the standalone profile can still expose a .env-only user contract.
    """

    env_name = str(config.get(f"{key}_env") or "").strip()
    return os.getenv(env_name, "").strip() if env_name else ""


@dataclass(frozen=True)
class Paths:
    root: Path
    raw: Path
    curated: Path
    staging_full: Path
    staging_update: Path
    staging_repair: Path
    metadata: Path
    output: Path
    quality: Path
    state: Path
    models: Path
    bronze: Path
    silver: Path
    gold: Path
    registry: Path
    qlib_versions: Path
    legacy: Path
    migration: Path

    @classmethod
    def from_root(cls, root: Path) -> "Paths":
        bronze = root / "bronze" / "market"
        return cls(
            root=root,
            raw=bronze / "current",
            curated=root / "silver" / "daily" / "current",
            staging_full=root / "gold" / "qlib_staging" / "full",
            staging_update=root / "gold" / "qlib_staging" / "update",
            staging_repair=root / "gold" / "qlib_staging" / "repair",
            metadata=root / "silver" / "reference" / "current",
            output=root / "output",
            quality=root / "quality",
            state=root / "state",
            models=root / "models",
            bronze=bronze,
            silver=root / "silver",
            gold=root / "gold",
            registry=root / "registry",
            qlib_versions=root / "qlib" / "versions",
            legacy=root / ".legacy",
            migration=root / ".migration",
        )

    @property
    def legacy_vendor_bronze(self) -> Path:
        """Pre-0.4 provider-coupled bronze root retained only for migration/readback."""

        return self.root / "bronze" / "tushare"

    @property
    def legacy_vendor_raw(self) -> Path:
        return self.legacy_vendor_bronze / "current"

    def mkdirs(self) -> None:
        for path in self.__dict__.values():
            Path(path).mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class Settings:
    config_path: Path
    data: dict[str, Any]
    paths: Paths
    # Deprecated constructor compatibility only; adapters resolve provider credentials.
    tushare_token: str | None
    qlib_repo: Path | None
    qlib_data_uri: Path

    @classmethod
    def load(
        cls,
        config_path: str | Path,
        *,
        require_tushare: bool = False,
        require_qlib_repo: bool = False,
        create_dirs: bool = True,
    ) -> "Settings":
        load_dotenv()
        config_path = resource_path(Path(config_path).expanduser()).resolve()
        data = _expand_env(_load_config(config_path))
        source_cfg = data.get("data_source", {})
        source_cfg = source_cfg if isinstance(source_cfg, dict) else {}
        if isinstance(data.get("tushare"), dict) and not isinstance(source_cfg.get("tushare"), dict):
            warnings.warn(
                "top-level tushare configuration is deprecated; move provider settings under data_source.tushare and retry/endpoint settings under data_source.runtime/optional_endpoints",
                DeprecationWarning,
                stacklevel=2,
            )

        if "project_root" not in data:
            raise ValueError("project_root is required")
        if "qlib" not in data:
            raise ValueError("qlib config is required")
        _require_mapping(data["qlib"], "qlib")
        _require_mapping(data.get("tushare", {}), "tushare")

        mode = str(data.get("mode") or "standalone").strip().lower()
        if mode not in {"standalone", "integrated"}:
            raise ValueError("mode must be standalone or integrated")
        root_env_name = str(data.get("project_root_env") or "").strip()
        root_override = os.getenv(root_env_name, "").strip() if root_env_name else ""
        project_root = Path(root_override or str(data["project_root"])).expanduser()
        if not project_root.is_absolute():
            project_root = (config_path.parent.parent / project_root).resolve()
        paths = Paths.from_root(project_root)
        if create_dirs:
            paths.mkdirs()

        token = os.getenv("TUSHARE_TOKEN", "").strip() or None
        if require_tushare and not token:
            raise RuntimeError("TUSHARE_TOKEN is not set. Copy .env.example to .env and fill the token.")

        qlib_cfg = data["qlib"]
        repo_raw = str(_configured_env_override(qlib_cfg, "repo_path") or qlib_cfg.get("repo_path") or "").strip()
        qlib_repo = Path(repo_raw).expanduser().resolve() if repo_raw else None
        if require_qlib_repo and (qlib_repo is None or not qlib_repo.exists()):
            raise RuntimeError("QLIB_REPO is not configured or does not exist")

        dataset_raw = str(
            _configured_env_override(qlib_cfg, "dataset_dir") or qlib_cfg.get("dataset_dir") or ""
        ).strip()
        if dataset_raw:
            qlib_data_uri = Path(dataset_raw).expanduser()
            if not qlib_data_uri.is_absolute():
                qlib_data_uri = (config_path.parent.parent / qlib_data_uri).resolve()
            else:
                qlib_data_uri = qlib_data_uri.resolve()
        else:
            qlib_data_uri = (paths.root / "qlib" / "current").resolve()

        return cls(config_path, data, paths, token, qlib_repo, qlib_data_uri)

    @property
    def data_source_config(self) -> dict[str, Any]:
        value = self.data.get("data_source", {})
        return value if isinstance(value, dict) else {}

    def provider_config(self, provider: str | None = None) -> dict[str, Any]:
        name = (provider or self.source_kind).strip().lower().replace("-", "_")
        value = self.data_source_config.get(name, {})
        if isinstance(value, dict):
            return value
        return {}

    def uses_tushare_source(self) -> bool:
        kind = self.source_kind
        if kind == "auto":
            # Auto resolution may use local immutable data first, but commands that
            # explicitly perform ingestion (daily-sync/bootstrap) fall back to TuShare.
            return True
        if kind in {"local", "qlib", "dataset"}:
            return False
        if kind in {"mysql", "lean_mysql", "lean-platform", "lean_platform"}:
            return False
        if kind in {"platform_release", "data_release"}:
            return False
        return True

    @property
    def source_kind(self) -> str:
        source_cfg = self.data.get("data_source", {})
        if not isinstance(source_cfg, dict):
            return "tushare"
        return str(source_cfg.get("kind", "tushare")).strip().lower()

    @property
    def mode(self) -> str:
        return str(self.data.get("mode") or "standalone").strip().lower()

    def uses_data_release(self) -> bool:
        return self.source_kind in {"platform_release", "data_release"}

    @property
    def data_release_config(self) -> dict[str, Any]:
        if not self.uses_data_release():
            raise ValueError("data_source.kind must be data_release or platform_release")
        source_cfg = _require_mapping(self.data.get("data_source", {}), "data_source")
        platform_cfg = source_cfg.get("platform_release")
        generic_cfg = source_cfg.get("data_release")
        if platform_cfg is not None and generic_cfg is not None and platform_cfg != generic_cfg:
            raise ValueError("conflicting data_source.platform_release and data_source.data_release")
        selected = generic_cfg if self.source_kind == "data_release" else platform_cfg
        if selected is None:
            selected = platform_cfg if platform_cfg is not None else generic_cfg
        return _require_mapping(selected or {}, "data_source.data_release")

    def uses_platform_release(self) -> bool:
        """Compatibility alias for callers that consume an immutable DataRelease."""

        return self.uses_data_release()

    @property
    def platform_release_config(self) -> dict[str, Any]:
        """Compatibility alias; new code should use data_release_config."""

        return self.data_release_config

    @property
    def platform_data_root(self) -> Path:
        raw = str(self.data_release_config.get("data_root") or "").strip()
        if not raw:
            store = self.data.get("release_store", {})
            raw = str(store.get("root") or "").strip() if isinstance(store, dict) else ""
        if not raw:
            raise ValueError("DataRelease data_root or release_store.root is required")
        path = Path(raw).expanduser()
        return path.resolve() if path.is_absolute() else (self.config_path.parent.parent / path).resolve()

    @property
    def platform_release_manifest(self) -> Path:
        raw = str(self.data_release_config.get("manifest") or "").strip()
        if not raw:
            release_id = str(
                self.data_release_config.get("id") or self.data_release_config.get("ref") or ""
            ).strip()
            if release_id.startswith("ds_"):
                raw = str(self.platform_data_root / release_id / "manifest.json")
        if not raw:
            raise ValueError("DataRelease manifest or immutable release id is required")
        path = Path(raw).expanduser()
        path = path if path.is_absolute() else self.config_path.parent.parent / path
        if path.is_symlink():
            raise FileNotFoundError(f"DataRelease manifest must not be a symlink: {path}")
        path = path.resolve()
        try:
            path.relative_to(self.platform_data_root)
        except ValueError as exc:
            raise ValueError("DataRelease manifest must remain under the configured data_root") from exc
        if not path.is_file():
            raise FileNotFoundError(f"DataRelease manifest is missing: {path}")
        return path

    @property
    def platform_qlib_staging_role(self) -> str:
        return str(self.data_release_config.get("qlib_staging_role") or "qlib_staging").strip()

    def require_qlib_repo(self) -> Path:
        if self.qlib_repo is None or not self.qlib_repo.exists():
            raise RuntimeError("QLIB_REPO is required and must exist for this command")
        return self.qlib_repo

    @property
    def registry_path(self) -> Path:
        storage = self.data.get("storage", {})
        configured = storage.get("registry_path") if isinstance(storage, dict) else None
        if not configured:
            return self.paths.registry / "qlib.sqlite"
        path = Path(str(configured)).expanduser()
        return path.resolve() if path.is_absolute() else (self.config_path.parent.parent / path).resolve()

    @property
    def qlib_versions_root(self) -> Path:
        configured = self.data.get("qlib", {}).get("versions_root")
        if not configured:
            return self.paths.qlib_versions
        path = Path(str(configured)).expanduser()
        return path.resolve() if path.is_absolute() else (self.config_path.parent.parent / path).resolve()

    @property
    def qlib_dataset_name(self) -> str:
        qlib_cfg = self.data.get("qlib", {})
        return str(qlib_cfg.get("dataset_name") or qlib_cfg.get("dataset_version") or "cn_tushare")

    @property
    def qlib_dataset_ref(self) -> str:
        return str(self.data.get("qlib", {}).get("dataset_ref") or "research-current")

    @property
    def qlib_include_fields(self) -> tuple[str, ...]:
        qlib = _require_mapping(self.data.get("qlib", {}), "qlib")
        base = qlib.get("include_fields", [])
        extra = qlib.get("include_fields_extra", [])
        if not isinstance(base, list) or not isinstance(extra, list):
            raise ValueError("qlib include_fields and include_fields_extra must be lists")
        return tuple(dict.fromkeys(str(value) for value in [*base, *extra]))