"""CLI composition package; command parsers are registered by bounded domain."""

from qlib_platform.cli.main import main
from qlib_platform.cli.parser import parser

__all__ = ["main", "parser"]
