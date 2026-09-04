"""Compatibility shim for the former phase-oriented module."""

from qlib_platform.research.contracts.stability_program import *  # noqa: F401,F403
from qlib_platform.research.contracts.stability_program import (  # noqa: F401
    _contains_final_holdout,
    _mapping,
    _sequence,
    _validate_data_release_acceptance,
    _validate_phase2_acceptance,
)
