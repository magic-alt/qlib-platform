from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Iterable, Mapping

import tomllib


DEFAULT_TAG = "py3-none-any"


def _project_root() -> Path:
    return Path(os.getcwd())


def _read_pyproject() -> dict[str, Any]:
    with (_project_root() / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


def _normalize_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9.]+", "_", value).strip("._")


def _project_metadata() -> tuple[str, str, list[str], list[str], dict[str, str] | None]:
    data = _read_pyproject()
    project = data.get("project", {})
    name = project.get("name", "tushare-qlib-a-share")
    version = project.get("version", "0.0.0")
    requires = project.get("dependencies", [])
    scripts = project.get("scripts", {})
    return name, str(version), list(requires), list(scripts.keys()), scripts


def _requires_dist_lines() -> list[str]:
    name, version, deps, _, _ = _project_metadata()
    del name, version
    lines: list[str] = []
    for dep in deps:
        lines.append(f"Requires-Dist: {dep}")
    return lines


def _dist_info_dir(name: str, version: str) -> str:
    return f"{_normalize_name(name)}-{version}.dist-info"


def _wheel_filename(name: str, version: str) -> str:
    return f"{_normalize_name(name)}-{version}-{DEFAULT_TAG}.whl"


def _top_level_packages() -> list[str]:
    src = _project_root() / "src"
    if not src.is_dir():
        return []

    packages: list[str] = []
    for child in src.iterdir():
        if child.is_dir() and (child / "__init__.py").exists():
            packages.append(child.name)
    return sorted(packages)


def _record_lines(entries: list[tuple[str, bytes]]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    for path, data in entries:
        digest = hashlib.sha256(data).digest()
        b64 = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        writer.writerow([path, f"sha256={b64}", str(len(data))])
    writer.writerow([f"{entries[-1][0].split('/',1)[0]}/RECORD", "", ""])
    return output.getvalue()


def _record_text(entries: list[tuple[str, bytes]], dist_info: str) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    for path, data in entries:
        digest = hashlib.sha256(data).digest()
        b64 = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        writer.writerow([path, f"sha256={b64}", str(len(data))])
    writer.writerow([f"{dist_info}/RECORD", "", ""])
    return output.getvalue()


def _metadata_text() -> str:
    data = _read_pyproject()
    project = data.get("project", {})
    name = project.get("name", "tushare-qlib-a-share")
    version = project.get("version", "0.0.0")
    description = project.get("description", "")
    requires_python = project.get("requires-python", "")

    lines = [
        "Metadata-Version: 2.1",
        f"Name: {name}",
        f"Version: {version}",
    ]
    if description:
        lines.append(f"Summary: {description}")
    if requires_python:
        lines.append(f"Requires-Python: {requires_python}")
    lines.extend(_requires_dist_lines())
    return "\n".join(lines) + "\n"


def _entry_points_text() -> str:
    _, _, _, _, scripts = _project_metadata()
    if not scripts:
        return ""

    lines = ["[console_scripts]"]
    _, _, _, _, script_entries = _project_metadata()
    for name, target in script_entries.items():
        lines.append(f"{name} = {target}")
    return "\n".join(lines) + "\n"


def _build_dist_info_files(name: str, version: str) -> dict[str, bytes]:
    dist_info = _dist_info_dir(name, version)
    metadata = _metadata_text()
    entry_points = _entry_points_text()
    top_level = "\n".join(_top_level_packages()) + ("\n" if _top_level_packages() else "")
    wheel_lines = (
        "Wheel-Version: 1.0\n"
        "Generator: local_build_meta\n"
        "Root-Is-Purelib: true\n"
        f"Tag: {DEFAULT_TAG}\n"
    )

    files: dict[str, bytes] = {
        f"{dist_info}/METADATA": metadata.encode("utf-8"),
        f"{dist_info}/WHEEL": wheel_lines.encode("utf-8"),
    }
    if top_level:
        files[f"{dist_info}/top_level.txt"] = top_level.encode("utf-8")
    if entry_points:
        files[f"{dist_info}/entry_points.txt"] = entry_points.encode("utf-8")
    return files


def _write_wheel(
    wheel_directory: str,
    *,
    editable: bool,
    metadata_directory: str | None = None,
) -> str:
    root = _project_root()
    name, version, _, _, _ = _project_metadata()
    dist_info = _dist_info_dir(name, version)

    files: dict[str, bytes] = _build_dist_info_files(name, version)

    if editable:
        pth_path = root / "src"
        qlib_path = root.parent / "qlib"
        pths = [str(pth_path)]
        if qlib_path.exists():
            pths.append(str(qlib_path))
        pth_text = "\n".join(dict.fromkeys(pths)) + "\n"
        files[f"{_normalize_name(name)}.pth"] = pth_text.encode("utf-8")
        files[f"{dist_info}/direct_url.json"] = json.dumps(
            {
                "url": root.resolve().as_uri(),
                "dir_info": {"editable": True},
            },
            ensure_ascii=False,
        ).encode("utf-8")

    if metadata_directory is not None:
        target_dir = Path(metadata_directory)
        target_dir.mkdir(parents=True, exist_ok=True)
        for path, data in files.items():
            if path.startswith(f"{dist_info}/"):
                out_path = target_dir / path
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(data)

    with tempfile.TemporaryDirectory() as tmpdir:
        for name_only in list(files):
            pass

        wheel_path = Path(wheel_directory).resolve()
        wheel_path.mkdir(parents=True, exist_ok=True)
        wheel_name = _wheel_filename(name, version)
        output_file = wheel_path / wheel_name

        record_rows = [(path, data) for path, data in files.items()]
        record_bytes = _record_text(record_rows, dist_info).encode("utf-8")
        files[f"{dist_info}/RECORD"] = record_bytes

        with zipfile.ZipFile(output_file, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for arc_name, data in files.items():
                zf.writestr(arc_name, data)

    return wheel_name


def _supported_features() -> list[str]:
    return ["build_editable"]


def get_requires_for_build_wheel(config_settings: Mapping[str, Any] | None = None) -> list[str]:
    return []


def get_requires_for_build_editable(config_settings: Mapping[str, Any] | None = None) -> list[str]:
    return []


def prepare_metadata_for_build_wheel(
    metadata_directory: str, config_settings: Mapping[str, Any] | None = None, _allow_fallback: bool = True
) -> str:
    root = _project_root()
    name, version, _, _, _ = _project_metadata()
    dist_info = _dist_info_dir(name, version)
    files = _build_dist_info_files(name, version)
    target_dir = Path(metadata_directory) / dist_info
    target_dir.mkdir(parents=True, exist_ok=True)
    for path, data in files.items():
        out_path = Path(metadata_directory) / path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)
    return dist_info


def prepare_metadata_for_build_editable(
    metadata_directory: str,
    config_settings: Mapping[str, Any] | None = None,
    _allow_fallback: bool = True,
) -> str:
    return prepare_metadata_for_build_wheel(metadata_directory, config_settings, _allow_fallback)


def build_wheel(
    wheel_directory: str,
    config_settings: Mapping[str, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    return _write_wheel(wheel_directory, editable=False, metadata_directory=metadata_directory)


def build_editable(
    wheel_directory: str,
    config_settings: Mapping[str, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    return _write_wheel(wheel_directory, editable=True, metadata_directory=metadata_directory)
