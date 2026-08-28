from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from tushare_qlib.docs_check import DocumentationFinding
from tushare_qlib.project_audit import _source_files, audit_project


def test_source_inventory_never_opens_env_files(tmp_path: Path, monkeypatch):
    env = tmp_path / ".env"
    source = tmp_path / "module.py"
    env.write_text("placeholder", encoding="utf-8")
    source.write_text("print('safe')", encoding="utf-8")
    monkeypatch.setattr(
        "tushare_qlib.project_audit.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=b".env\0module.py\0",
        ),
    )

    assert _source_files(tmp_path) == [source]


def test_project_audit_includes_documentation_findings(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("tushare_qlib.project_audit._source_files", lambda _base: [])
    monkeypatch.setattr(
        "tushare_qlib.project_audit.check_documentation",
        lambda _base: [
            DocumentationFinding(
                rule_id="DOC-TEST",
                severity="P1",
                path="docs/example.md",
                line=7,
                message="example drift",
            )
        ],
    )

    report = audit_project(tmp_path)

    finding = next(item for item in report["findings"] if item["id"] == "DOC-TEST")
    assert finding["passed"] is False
    assert finding["evidence"] == "location=docs/example.md:7; issue=example drift"
