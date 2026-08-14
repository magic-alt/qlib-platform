from __future__ import annotations

import json
from typing import Mapping

from .base import AlphaPackSpec
from ..fundamentals import PIT_FIELDS
from ..settings import Settings


_BASE_FIELDS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "money",
    "vwap",
    "factor",
)
_DAILY_FIELDS = (
    "turnover_rate_f",
    "volume_ratio",
    "circ_mv",
    "pe_ttm",
    "pb",
    "ps_ttm",
    "dv_ttm",
    "money",
    "net_mf_amount",
    "big_net_amount",
    "paused",
    "is_st",
    "listed_days",
    "is_limit_up",
    "is_limit_down",
)

ALPHA_PACKS: dict[str, AlphaPackSpec] = {
    "alpha158_daily_v1": AlphaPackSpec(
        "alpha158_daily_v1",
        1,
        "TushareAlpha158Daily",
        (*_BASE_FIELDS, *_DAILY_FIELDS),
        (),
        60,
        "alpha158_default_v1",
        ("technical", "liquidity", "valuation", "state"),
    ),
    "alpha158_pit_v1": AlphaPackSpec(
        "alpha158_pit_v1",
        1,
        "TushareAlpha158Fundamental",
        (*_BASE_FIELDS, *_DAILY_FIELDS, *PIT_FIELDS),
        ("pit_fundamentals",),
        60,
        "alpha158_default_v1",
        ("technical", "liquidity", "valuation", "state", "profitability", "growth", "leverage"),
    ),
    "multifactor_core_v1": AlphaPackSpec(
        "multifactor_core_v1",
        1,
        "TushareMultiFactorCore",
        (*_BASE_FIELDS, *_DAILY_FIELDS, "industry_l1_code", *PIT_FIELDS),
        ("pit_fundamentals", "industry_classification_pit"),
        60,
        "multifactor_cross_section_v1",
        (
            "momentum",
            "value",
            "quality",
            "growth",
            "size",
            "liquidity",
            "low_volatility",
            "reversal",
            "profitability",
            "leverage",
            "cash_flow",
        ),
    ),
}


def get_alpha_pack(pack_id: str) -> AlphaPackSpec:
    try:
        return ALPHA_PACKS[pack_id]
    except KeyError as exc:
        raise ValueError(f"unknown alpha pack: {pack_id}") from exc


def alpha_pack_from_settings(settings: Settings) -> AlphaPackSpec:
    experiment = settings.data.get("experiment", {})
    alpha = experiment.get("alpha", {}) if isinstance(experiment, Mapping) else {}
    configured = alpha.get("pack") if isinstance(alpha, Mapping) else None
    return get_alpha_pack(str(configured or "alpha158_pit_v1"))


def assert_alpha_pack_compatible(settings: Settings, pack: AlphaPackSpec) -> None:
    manifest_path = settings.qlib_data_uri / "dataset_manifest.json"
    if manifest_path.is_file():
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        fields = set(payload.get("fields") or payload.get("semantic_contract", {}).get("fields") or [])
        missing_fields = sorted(set(pack.required_qlib_fields) - fields) if fields else []
        if missing_fields:
            raise ValueError(f"alpha pack {pack.pack_id} missing Qlib fields: {missing_fields}")
    if settings.uses_platform_release() and pack.required_release_components:
        release = json.loads(settings.platform_release_manifest.read_text(encoding="utf-8"))
        roles = {str(item.get("role")) for item in release.get("components", [])}
        missing = sorted(set(pack.required_release_components) - roles)
        if missing:
            raise ValueError(f"alpha pack {pack.pack_id} missing DataRelease components: {missing}")


def handler_class(pack: AlphaPackSpec):
    from .. import custom_handler

    return getattr(custom_handler, pack.handler_class)
