from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

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


@dataclass(frozen=True)
class Paths:
    root: Path
    raw: Path
    raw_revisions: Path
    curated: Path
    staging_full: Path
    staging_update: Path
    staging_repair: Path
    metadata: Path
    output: Path
    quality: Path
    state: Path
    models: Path

    @classmethod
    def from_root(cls, root: Path) -> "Paths":
        return cls(
            root=root,
            raw=root / "raw",
            raw_revisions=root / "raw_revisions",
            curated=root / "curated" / "daily",
            staging_full=root / "staging" / "full",
            staging_update=root / "staging" / "update",
            staging_repair=root / "staging" / "repair",
            metadata=root / "metadata",
            output=root / "output",
            quality=root / "quality",
            state=root / "state",
            models=root / "models",
        )

    def mkdirs(self) -> None:
        for path in self.__dict__.values():
            Path(path).mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class Settings:
    config_path: Path
    data: dict[str, Any]
    paths: Paths
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
        config_path = Path(config_path).expanduser().resolve()
        with config_path.open("r", encoding="utf-8") as fp:
            loaded = yaml.safe_load(fp)
        data = _expand_env(_require_mapping(loaded, "root config"))

        if "project_root" not in data:
            raise ValueError("project_root is required")
        if "qlib" not in data:
            raise ValueError("qlib config is required")
        _require_mapping(data["qlib"], "qlib")
        _require_mapping(data.get("tushare", {}), "tushare")

        project_root = Path(str(data["project_root"])).expanduser()
        if not project_root.is_absolute():
            project_root = (config_path.parent.parent / project_root).resolve()
        paths = Paths.from_root(project_root)
        if create_dirs:
            paths.mkdirs()

        token = os.getenv("TUSHARE_TOKEN", "").strip() or None
        if require_tushare and not token:
            raise RuntimeError("TUSHARE_TOKEN is not set. Copy .env.example to .env and fill the token.")

        qlib_cfg = data["qlib"]
        repo_raw = str(qlib_cfg.get("repo_path", "")).strip()
        qlib_repo = Path(repo_raw).expanduser().resolve() if repo_raw else None
        if require_qlib_repo and (qlib_repo is None or not qlib_repo.exists()):
            raise RuntimeError("QLIB_REPO is not configured or does not exist")

        dataset_raw = str(qlib_cfg.get("dataset_dir", "")).strip()
        if not dataset_raw:
            raise ValueError("qlib.dataset_dir is required")
        qlib_data_uri = Path(dataset_raw).expanduser()
        if not qlib_data_uri.is_absolute():
            qlib_data_uri = (config_path.parent.parent / qlib_data_uri).resolve()
        else:
            qlib_data_uri = qlib_data_uri.resolve()

        return cls(config_path, data, paths, token, qlib_repo, qlib_data_uri)

    def require_token(self) -> str:
        if not self.tushare_token:
            raise RuntimeError("TUSHARE_TOKEN is required for this command")
        return self.tushare_token

    def uses_tushare_source(self) -> bool:
        source_cfg = self.data.get("data_source", {})
        if not isinstance(source_cfg, dict):
            return True
        kind = str(source_cfg.get("kind", "tushare")).strip().lower()
        if kind == "auto":
            return "mysql" not in source_cfg
        if kind in {"mysql", "lean_mysql", "lean-platform", "lean_platform"}:
            return False
        return True

    def require_qlib_repo(self) -> Path:
        if self.qlib_repo is None or not self.qlib_repo.exists():
            raise RuntimeError("QLIB_REPO is required and must exist for this command")
        return self.qlib_repo
