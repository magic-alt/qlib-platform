from __future__ import annotations

import ast
from pathlib import Path

from qlib_platform.settings import Paths


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "qlib_platform"


def test_package_root_is_a_small_composition_boundary():
    allowed = {
        "__init__.py",
        "__main__.py",
        "bootstrap.py",
        "canonical_config.py",
        "docs_check.py",
        "lineage.py",
        "project_audit.py",
        "settings.py",
        "workflow_contract.py",
    }
    files = {path.name for path in PACKAGE.iterdir() if path.is_file()}
    assert files <= allowed


def test_transitional_namespaces_and_shims_are_removed():
    assert not (ROOT / "src" / "tushare_qlib").exists()
    assert not (PACKAGE / "data" / "sources" / "client.py").exists()
    assert not list(PACKAGE.rglob("_legacy_*.py"))


def test_ingestion_does_not_import_concrete_provider_modules():
    path = PACKAGE / "data" / "ingestion.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert "qlib_platform.data.sources.tushare" not in imported
    assert "qlib_platform.data.sources.mysql" not in imported


def test_canonical_storage_identity_is_provider_neutral(tmp_path: Path):
    paths = Paths.from_root(tmp_path / "data")
    assert paths.bronze == tmp_path / "data" / "bronze" / "market"
    assert paths.raw == paths.bronze / "current"


def test_cli_is_composed_from_domain_registrars():
    parser_file = PACKAGE / "cli" / "parser.py"
    assert parser_file.is_file()
    for domain in ("data", "datasets", "research", "backtesting", "runtime", "ops", "releases"):
        assert (PACKAGE / "cli" / "commands" / f"{domain}.py").is_file()
