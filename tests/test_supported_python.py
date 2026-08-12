from __future__ import annotations

import sys
import tomllib
from pathlib import Path


def test_running_interpreter_is_in_supported_ci_range():
    assert (3, 10) <= sys.version_info[:2] <= (3, 12)


def test_project_python_contract_matches_ci_matrix():
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert project["project"]["requires-python"] == ">=3.10,<3.13"
    assert "python: ['3.10', '3.11', '3.12']" in workflow
    assert "os: [ubuntu-latest, windows-latest]" in workflow
    assert "constraints/ci.txt" in workflow
