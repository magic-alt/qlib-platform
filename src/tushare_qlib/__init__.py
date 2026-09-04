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
# tree.  Materialize it as a list to satisfy the package ``__path__`` contract
# across Python/importlib implementations while keeping the namespace read-only.
__path__ = list(_canonical.__path__)
