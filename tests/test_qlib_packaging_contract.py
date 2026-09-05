from __future__ import annotations

import tomllib
from pathlib import Path


def test_qlib_is_core_and_heavy_capabilities_are_explicit_extras() -> None:
    payload = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    project = payload["project"]
    dependencies = project["dependencies"]
    extras = project["optional-dependencies"]

    assert "pyqlib==0.9.7" in dependencies
    assert "tq-qlib" in project["scripts"]
    assert project["scripts"]["tq-qlib"] == "qlib_platform.qlib_compat.cli:main"

    qlib_full = set(extras["qlib-full"])
    for prefix in (
        "lightgbm",
        "xgboost",
        "torch",
        "catboost",
        "scikit-learn",
        "scipy",
        "tianshou",
        "hyperopt",
        "plotly",
        "statsmodels",
    ):
        assert any(item.startswith(prefix) for item in qlib_full)

    assert any(item.startswith("tianshou") for item in extras["qlib-rl"])
    assert any(item.startswith("plotly") for item in extras["qlib-analysis"])
    assert any(item.startswith("hyperopt") for item in extras["qlib-tuner"])
