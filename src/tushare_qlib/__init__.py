"""Backward-compatible import namespace for :mod:`qlib_platform`.

New code should import ``qlib_platform``.  This package keeps existing scripts,
configs, and downstream users working while the provider-neutral package name is
adopted.
"""

from __future__ import annotations

from importlib import import_module

_canonical = import_module("qlib_platform")

__version__ = _canonical.__version__
__all__ = ["__version__"]

# Reuse the canonical package search path so imports such as
# ``tushare_qlib.research`` continue to resolve without duplicating the source
# tree.  This is a compatibility surface only; implementations live under
# ``qlib_platform``.
__path__ = _canonical.__path__
