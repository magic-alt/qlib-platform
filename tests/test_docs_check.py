from __future__ import annotations

from pathlib import Path

import pytest

from tushare_qlib.docs_check import check_documentation


ROOT = Path(__file__).resolve().parents[1]


def _write_active_doc(root: Path, body: str) -> Path:
    path = root / "docs" / "guide.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\n"
        "status: ACTIVE\n"
        "owner: testing\n"
        "applies_to_commit: test\n"
        "last_verified: 2026-08-28\n"
        "---\n\n"
        "# Guide\n\n"
        f"{body}\n",
        encoding="utf-8",
    )
    return path


def test_repository_documentation_baseline_is_clean():
    assert check_documentation(ROOT) == []


def test_accepts_governed_doc_with_existing_link_and_cli(tmp_path: Path):
    _write_active_doc(tmp_path, "& $RepoPython -m tushare_qlib status")
    (tmp_path / "README.md").write_text("[Guide](docs/guide.md)\n", encoding="utf-8")

    assert check_documentation(tmp_path) == []


@pytest.mark.parametrize(
    ("body", "rule_id"),
    [
        ("[missing](missing.md)", "DOC-001"),
        (r"$RepoPython = '.\.venv\python.exe'", "DOC-004"),
        ("/Users/example/qlib/data", "DOC-005"),
        ("& $RepoPython -m tushare_qlib command-that-does-not-exist", "DOC-003"),
    ],
)
def test_rejects_common_documentation_drift(tmp_path: Path, body: str, rule_id: str):
    _write_active_doc(tmp_path, body)

    assert rule_id in {item.rule_id for item in check_documentation(tmp_path)}


def test_rejects_missing_governance_header(tmp_path: Path):
    path = tmp_path / "docs" / "guide.md"
    path.parent.mkdir(parents=True)
    path.write_text("# Ungoverned\n", encoding="utf-8")

    assert "DOC-007" in {item.rule_id for item in check_documentation(tmp_path)}


def test_rejects_active_execution_plane_manual(tmp_path: Path):
    path = _write_active_doc(tmp_path, "Execution detail")
    target = path.with_name("qmt_manual.md")
    path.rename(target)

    assert "ARCH-002" in {item.rule_id for item in check_documentation(tmp_path)}


def test_rejects_non_portable_workflow_provider(tmp_path: Path):
    workflow = tmp_path / "workflow_local.yaml"
    workflow.write_text('provider_uri: "/workstation/data"\n', encoding="utf-8")

    assert "DOC-005" in {item.rule_id for item in check_documentation(tmp_path)}


def test_rejects_direct_historical_link_from_current_entry_point(tmp_path: Path):
    target = tmp_path / "docs" / "history" / "research" / "alpha_research_phase_1.md"
    target.parent.mkdir(parents=True)
    target.write_text(
        "---\n"
        "status: HISTORICAL\n"
        "owner: research\n"
        "applies_to_commit: test\n"
        "last_verified: 2026-08-28\n"
        "---\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(
        "[Phase 1](docs/history/research/alpha_research_phase_1.md)\n",
        encoding="utf-8",
    )

    assert "DOC-008" in {item.rule_id for item in check_documentation(tmp_path)}
