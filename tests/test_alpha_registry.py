from __future__ import annotations

import pytest

from tushare_qlib.alpha import ALPHA_PACKS, get_alpha_pack


def test_first_alpha_pack_set_is_registered_with_stable_contracts():
    assert set(ALPHA_PACKS) == {
        "alpha158_daily_v1",
        "alpha158_pit_v1",
        "multifactor_core_v1",
    }
    assert len({pack.fingerprint for pack in ALPHA_PACKS.values()}) == 3
    assert "industry_classification_pit" in ALPHA_PACKS["multifactor_core_v1"].required_release_components
    assert ALPHA_PACKS["alpha158_pit_v1"].processor_recipe == "alpha158_default_v1"


def test_unknown_alpha_pack_fails_closed():
    with pytest.raises(ValueError, match="unknown alpha pack"):
        get_alpha_pack("future_alpha")
