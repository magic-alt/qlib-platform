from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "src" / "qlib_platform" / "research"
LEGACY_IMPORT = re.compile(r"qlib_platform\.research\.phase[123]_")
LEGACY_COMMAND = re.compile(r"phase[123]-")
LEGACY_IDENTIFIER = re.compile(r"^phase[123](?:_|$)", re.IGNORECASE)
LEGACY_MODULE_FILE = re.compile(r"(?:src/)?qlib_platform/research/phase[123]_[A-Za-z0-9_.-]*\.py")


def _text_files(root: Path):
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in {
            ".py",
            ".md",
            ".yml",
            ".yaml",
            ".toml",
            ".ps1",
            ".sh",
        }:
            yield path


def _legacy_python_identifiers(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        candidates: list[str] = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            candidates.append(node.name)
        elif isinstance(node, ast.Name):
            candidates.append(node.id)
        elif isinstance(node, ast.arg):
            candidates.append(node.arg)
        elif isinstance(node, ast.Attribute):
            candidates.append(node.attr)
        elif isinstance(node, ast.alias):
            candidates.extend(part for part in (node.name.rsplit(".", 1)[-1], node.asname) if part)
        names.update(name for name in candidates if LEGACY_IDENTIFIER.match(name))
    return sorted(names)


def test_research_runtime_has_no_phase_named_modules() -> None:
    offenders = sorted(path.relative_to(ROOT).as_posix() for path in RESEARCH.glob("phase[123]_*.py"))
    assert offenders == []


def test_repository_has_no_legacy_research_module_imports() -> None:
    offenders = []
    for path in _text_files(ROOT):
        text = path.read_text(encoding="utf-8")
        if LEGACY_IMPORT.search(text):
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


def test_repository_has_no_deleted_phase_module_file_references() -> None:
    offenders = []
    for path in _text_files(ROOT):
        text = path.read_text(encoding="utf-8")
        if LEGACY_MODULE_FILE.search(text):
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


def test_research_python_identifiers_are_stage_neutral() -> None:
    offenders: dict[str, list[str]] = {}
    for path in RESEARCH.rglob("*.py"):
        names = _legacy_python_identifiers(path)
        if names:
            offenders[path.relative_to(ROOT).as_posix()] = names
    assert offenders == {}


def test_cli_has_no_phase_numbered_commands() -> None:
    parser_source = (ROOT / "src/qlib_platform/cli/commands/research.py").read_text(encoding="utf-8")
    assert LEGACY_COMMAND.search(parser_source) is None


def test_research_root_contains_only_package_boundary_files() -> None:
    runtime_files = sorted(path.name for path in RESEARCH.glob("*.py"))
    assert runtime_files == ["__init__.py"]
