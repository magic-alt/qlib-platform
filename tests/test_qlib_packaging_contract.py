from __future__ import annotations

from pathlib import Path


def test_qlib_is_core_and_heavy_capabilities_are_explicit_extras() -> None:
    text = Path("pyproject.toml").read_text(encoding="utf-8")

    assert '"pyqlib==0.9.7"' in text
    assert 'tq-qlib = "qlib_platform.qlib_compat.cli:main"' in text

    qlib_full = text.split("qlib-full = [", 1)[1].split("]", 1)[0]
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
        assert f'"{prefix}' in qlib_full

    qlib_rl = text.split("qlib-rl = [", 1)[1].split("]", 1)[0]
    qlib_analysis = text.split("qlib-analysis = [", 1)[1].split("]", 1)[0]
    qlib_tuner = text.split("qlib-tuner = [", 1)[1].split("]", 1)[0]
    assert '"tianshou' in qlib_rl
    assert '"plotly' in qlib_analysis
    assert '"hyperopt' in qlib_tuner
