from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    return value


@dataclass(frozen=True)
class Paths:
    root: Path
    raw: Path
    curated: Path
    staging_full: Path
    staging_update: Path
    metadata: Path
    output: Path

    @classmethod
    def from_root(cls, root: Path) -> "Paths":
        return cls(
            root=root,
            raw=root / "raw",
            curated=root / "curated" / "daily",
            staging_full=root / "staging" / "full",
            staging_update=root / "staging" / "update",
            metadata=root / "metadata",
            output=root / "output",
        )

    def mkdirs(self) -> None:
        for path in self.__dict__.values():
            Path(path).mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class Settings:
    config_path: Path
    data: dict[str, Any]
    paths: Paths
    tushare_token: str
    qlib_repo: Path
    qlib_data_uri: Path

    @classmethod
    def load(cls, config_path: str | Path) -> "Settings":
        load_dotenv()
        config_path = Path(config_path).expanduser().resolve()
        with config_path.open("r", encoding="utf-8") as fp:
            data = _expand_env(yaml.safe_load(fp))

        project_root = Path(data["project_root"])
        if not project_root.is_absolute():
            project_root = (config_path.parent.parent / project_root).resolve()
        paths = Paths.from_root(project_root)
        paths.mkdirs()

        token = os.getenv("TUSHARE_TOKEN", "").strip()
        if not token:
            raise RuntimeError("TUSHARE_TOKEN is not set. Copy .env.example to .env and fill the token.")

        qlib_repo = Path(data["qlib"]["repo_path"]).expanduser().resolve()
        qlib_data_uri = Path(data["qlib"]["dataset_dir"]).expanduser().resolve()
        return cls(config_path, data, paths, token, qlib_repo, qlib_data_uri)
