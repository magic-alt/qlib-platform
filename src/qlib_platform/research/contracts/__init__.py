"""Frozen research contracts and design locks."""

from qlib_platform.research.contracts.research_profile import (
    DEFAULT_RESEARCH_PROFILE_ID,
    RESEARCH_PROFILES,
    CalendarVersion,
    InstrumentSpec,
    LabelDefinition,
    ResearchProfile,
    SymbolAlias,
    assert_information_available,
    describe_research_profile,
    legacy_ashare_instrument,
    require_research_profile,
    research_profile_from_settings,
)

__all__ = [
    "DEFAULT_RESEARCH_PROFILE_ID",
    "RESEARCH_PROFILES",
    "CalendarVersion",
    "InstrumentSpec",
    "LabelDefinition",
    "ResearchProfile",
    "SymbolAlias",
    "assert_information_available",
    "describe_research_profile",
    "legacy_ashare_instrument",
    "require_research_profile",
    "research_profile_from_settings",
]
