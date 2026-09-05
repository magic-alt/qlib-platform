from __future__ import annotations

import pytest

from qlib_platform.alpha import ALPHA_PACKS, get_alpha_pack


def test_first_alpha_pack_set_is_registered_with_stable_contracts():
    assert set(ALPHA_PACKS) == {
        "qlib_alpha158_official_v1",
        "alpha158_daily_v1",
        "alpha158_market_v1",
        "alpha158_pit_v1",
        "multifactor_core_v1",
        "ashare_factor_benchmark_v1",
        "ashare_alpha_phase2_v1",
    }
    assert len({pack.fingerprint for pack in ALPHA_PACKS.values()}) == len(ALPHA_PACKS)
    assert "industry_classification_pit" in ALPHA_PACKS["multifactor_core_v1"].required_release_components
    market_pack = ALPHA_PACKS["alpha158_market_v1"]
    assert market_pack.required_release_components == ()
    assert market_pack.feature_groups == ("technical",)
    assert "close" in market_pack.required_qlib_fields
    assert ALPHA_PACKS["alpha158_pit_v1"].processor_recipe == "alpha158_default_v1"

    official_pack = ALPHA_PACKS["qlib_alpha158_official_v1"]
    assert official_pack.handler_class == "QlibOfficialAlpha158"
    assert official_pack.processor_recipe == "qlib_official_alpha158_v1"
    assert official_pack.required_release_components == ()
    assert official_pack.feature_groups == ("technical",)
    assert set(official_pack.required_qlib_fields) >= {"open", "high", "low", "close", "volume", "vwap"}


def test_unknown_alpha_pack_fails_closed():
    with pytest.raises(ValueError, match="unknown alpha pack"):
        get_alpha_pack("future_alpha")
