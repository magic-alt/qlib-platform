from __future__ import annotations

import importlib


def test_provider_neutral_package_is_canonical():
    package = importlib.import_module("qlib_platform")
    assert package.__version__ == "0.3.0"


def test_legacy_namespace_resolves_canonical_modules():
    legacy = importlib.import_module("tushare_qlib")
    module = importlib.import_module("tushare_qlib.strategy_contract")

    assert legacy.__version__ == "0.3.0"
    assert module.__file__ is not None
    assert "qlib_platform" in module.__file__.replace("\\", "/")
