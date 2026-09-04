from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "src" / "qlib_platform" / "research"
LEGACY_IMPORT = re.compile(r"qlib_platform\.research\.phase[123]_")
LEGACY_COMMAND = re.compile(r"phase[123]-")


def _text_files(root: Path):
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".py", ".md", ".yml", ".yaml", ".toml", ".ps1", ".sh"}:
            yield path


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


def test_cli_has_no_phase_numbered_commands() -> None:
    parser_source = (ROOT / "src/qlib_platform/cli/commands/research.py").read_text(encoding="utf-8")
    assert LEGACY_COMMAND.search(parser_source) is None


def test_research_root_contains_only_package_boundary_files() -> None:
    runtime_files = sorted(path.name for path in RESEARCH.glob("*.py"))
    assert runtime_files == ["__init__.py"]
