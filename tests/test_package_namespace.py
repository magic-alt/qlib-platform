from __future__ import annotations

import importlib
from pathlib import Path

import pytest


def test_provider_neutral_package_is_canonical():
    package = importlib.import_module("qlib_platform")
    assert package.__version__ == "0.3.0"


def test_vendor_named_legacy_namespace_is_removed():
    repository_root = Path(__file__).resolve().parents[1]
    assert not (repository_root / "src" / "tushare_qlib").exists()
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("tushare_qlib")


def test_domain_modules_resolve_from_canonical_namespace():
    module = importlib.import_module("qlib_platform.backtesting.strategy_contract")
    assert module.__file__ is not None
    assert "qlib_platform/backtesting" in module.__file__.replace("\\", "/")
