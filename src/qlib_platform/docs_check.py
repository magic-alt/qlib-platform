from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import unquote

import yaml


@dataclass(frozen=True)
class DocumentationFinding:
    rule_id: str
    severity: str
    path: str
    line: int
    message: str


_STATUS_VALUES = {"ACTIVE", "FROZEN", "HISTORICAL", "DEPRECATED", "MOVED"}
_NESTED_COMMANDS = {"auth", "health", "outbox", "release"}
_LINK_RE = re.compile(r"!?(?:\[[^\]]*\])\(([^)]+)\)")
_BAD_WINDOWS_PYTHON = re.compile(r"(?i)\.venv[\\/]python\.exe")
_PERSONAL_PATH = re.compile(r"(?i)(?:/Users/[^/\s]+/|[A-Z]:[\\/]Users[\\/][^\\/\s]+[\\/])")
_CLI_RE = re.compile(
    r"-m\s+tushare_qlib(?:\s+--config\s+\S+)?\s+([a-z][a-z0-9-]*)"
    r"(?:\s+([a-z][a-z0-9-]*))?"
)


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _front_matter(text: str) -> dict[str, object] | None:
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end < 0:
        return None
    value = yaml.safe_load(text[4:end])
    return value if isinstance(value, dict) else None


def _document_status(text: str) -> str | None:
    metadata = _front_matter(text)
    return str(metadata.get("status")) if metadata else None


def _commands(parser: argparse.ArgumentParser) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for name, child in action.choices.items():
            nested: set[str] = set()
            for child_action in child._actions:
                if isinstance(child_action, argparse._SubParsersAction):
                    nested.update(child_action.choices)
                elif not child_action.option_strings and child_action.choices is not None:
                    nested.update(str(choice) for choice in child_action.choices)
            result[name] = nested
    return result


def _markdown_findings(root: Path, files: list[Path]) -> list[DocumentationFinding]:
    findings: list[DocumentationFinding] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(root).as_posix()
        metadata = _front_matter(text)
        if path.parts[-2] == "docs" or "docs" in path.relative_to(root).parts[:-1]:
            if metadata is None:
                findings.append(
                    DocumentationFinding(
                        "DOC-007",
                        "P1",
                        relative,
                        1,
                        "governed document has no front matter",
                    )
                )
            else:
                status = str(metadata.get("status") or "")
                if status not in _STATUS_VALUES:
                    findings.append(
                        DocumentationFinding("DOC-007", "P1", relative, 1, "invalid document status")
                    )
                for key in ("owner", "applies_to_commit", "last_verified"):
                    if not metadata.get(key):
                        findings.append(DocumentationFinding("DOC-007", "P1", relative, 1, f"missing {key}"))
                try:
                    date.fromisoformat(str(metadata.get("last_verified")))
                except ValueError:
                    findings.append(
                        DocumentationFinding("DOC-007", "P1", relative, 1, "last_verified is not ISO date")
                    )

        for match in _LINK_RE.finditer(text):
            raw_target = match.group(1).strip().split(maxsplit=1)[0].strip("<>")
            if not raw_target or raw_target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            target_text = unquote(raw_target.split("#", 1)[0])
            target = (path.parent / target_text).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                findings.append(
                    DocumentationFinding(
                        "DOC-001",
                        "P1",
                        relative,
                        _line_number(text, match.start()),
                        "link escapes repository",
                    )
                )
                continue
            if target.is_dir():
                target = target / "README.md"
            if not target.exists():
                findings.append(
                    DocumentationFinding(
                        "DOC-001", "P1", relative, _line_number(text, match.start()), "link target is missing"
                    )
                )
    return findings


def _cli_findings(root: Path, files: list[Path]) -> list[DocumentationFinding]:
    from .cli import parser

    known = _commands(parser())
    findings: list[DocumentationFinding] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        status = _document_status(text)
        if status in {"HISTORICAL", "DEPRECATED", "MOVED"}:
            continue
        if "docs" in path.relative_to(root).parts[:-1] and status not in {"ACTIVE", "FROZEN"}:
            continue
        relative = path.relative_to(root).as_posix()
        for number, line in enumerate(text.splitlines(), start=1):
            for match in _CLI_RE.finditer(line):
                command, nested = match.groups()
                if command not in known:
                    findings.append(
                        DocumentationFinding("DOC-003", "P1", relative, number, "unknown CLI command")
                    )
                elif command in _NESTED_COMMANDS and nested not in known[command]:
                    findings.append(
                        DocumentationFinding("DOC-003", "P1", relative, number, "unknown nested CLI command")
                    )
    return findings


def _portable_path_findings(root: Path) -> list[DocumentationFinding]:
    findings: list[DocumentationFinding] = []
    candidates = [
        *root.glob("*.md"),
        *root.glob("*.yaml"),
        *root.glob("docs/**/*.md"),
        *root.glob("examples/**/*.md"),
        *root.glob("examples/**/*.ps1"),
        *root.glob("scripts/**/*.md"),
        *root.glob("scripts/**/*.ps1"),
        *root.glob("configs/workflow*.yaml"),
        *root.glob("contracts/**/*.md"),
        *root.glob(".agents/**/*.md"),
    ]
    for path in sorted(set(candidate.resolve() for candidate in candidates if candidate.is_file())):
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(root).as_posix()
        for rule_id, severity, pattern, message in (
            ("DOC-004", "P0", _BAD_WINDOWS_PYTHON, "non-standard Windows venv interpreter path"),
            ("DOC-005", "P0", _PERSONAL_PATH, "workstation-specific absolute path"),
        ):
            for match in pattern.finditer(text):
                findings.append(
                    DocumentationFinding(
                        rule_id, severity, relative, _line_number(text, match.start()), message
                    )
                )
    for path in sorted(root.glob("**/workflow*.yaml")):
        if any(part in {".venv", "data", "mlruns"} for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            if "provider_uri:" in line and "QLIB_DATA_URI" not in line:
                findings.append(
                    DocumentationFinding(
                        "DOC-005",
                        "P0",
                        path.relative_to(root).as_posix(),
                        number,
                        "workflow provider_uri is not portable",
                    )
                )
    return findings


def _version_findings(root: Path) -> list[DocumentationFinding]:
    pyproject = root / "pyproject.toml"
    build_script = root / "scripts" / "build_lightgbm_opencl_windows.ps1"
    constraints = root / "constraints" / "ci.txt"
    if not pyproject.is_file() or not build_script.is_file() or not constraints.is_file():
        return []
    project_text = pyproject.read_text(encoding="utf-8")
    project_match = re.search(r'"lightgbm==([^"]+)"', project_text)
    script_text = build_script.read_text(encoding="utf-8")
    script_match = re.search(r'LightGBMVersion\s*=\s*"([^"]+)"', script_text)
    constraints_match = re.search(r"(?m)^lightgbm==([^\s]+)$", constraints.read_text(encoding="utf-8"))
    versions = {
        project_match.group(1) if project_match else None,
        script_match.group(1) if script_match else None,
        constraints_match.group(1) if constraints_match else None,
    }
    if len(versions) == 1 and None not in versions:
        return []
    return [
        DocumentationFinding(
            "DOC-006", "P0", "scripts/build_lightgbm_opencl_windows.ps1", 1, "LightGBM version drift"
        )
    ]


def _governance_findings(root: Path, markdown: list[Path]) -> list[DocumentationFinding]:
    findings: list[DocumentationFinding] = []
    for name in ("README.md", "docs/index.md", "docs/current_state.md"):
        path = root / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        historical_link = re.compile(
            r"\((?:docs/)?(?:history/(?:research|qrun)/)?"
            r"(?:alpha_research_phase_[12]|p0_research_baseline|"
            r"local_alpha158_qrun_backtest)\.md"
        )
        if historical_link.search(text):
            findings.append(
                DocumentationFinding(
                    "DOC-008", "P1", name, 1, "current entry point links directly to historical document"
                )
            )
    for path in markdown:
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(root).as_posix()
        execution_manual = re.search(
            r"(?i)(?:^|/)(?:qmt|oms|broker|ledger)",
            relative,
        )
        if _document_status(text) == "ACTIVE" and execution_manual:
            findings.append(
                DocumentationFinding("ARCH-002", "P0", relative, 1, "execution-plane manual is marked ACTIVE")
            )
    return findings


def check_documentation(root: str | Path) -> list[DocumentationFinding]:
    base = Path(root).expanduser().resolve()
    markdown_candidates = [
        *base.glob("*.md"),
        *base.glob("docs/**/*.md"),
        *base.glob("examples/**/*.md"),
        *base.glob("scripts/**/*.md"),
        *base.glob("contracts/**/*.md"),
        *base.glob(".agents/**/*.md"),
    ]
    markdown = sorted({path.resolve() for path in markdown_candidates if path.is_file()})
    return sorted(
        [
            *_markdown_findings(base, markdown),
            *_cli_findings(base, markdown),
            *_portable_path_findings(base),
            *_version_findings(base),
            *_governance_findings(base, markdown),
        ],
        key=lambda item: (item.rule_id, item.path, item.line, item.message),
    )
