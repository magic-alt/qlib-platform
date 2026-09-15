from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping

from qlib_platform.alpha.base import AlphaPackSpec
from qlib_platform.lineage import sha256_json
from qlib_platform.research.contracts.ashare_etf import ETF_REQUIRED_DATASETS
from qlib_platform.research.contracts.research_profile import ResearchProfile


@dataclass(frozen=True)
class AlphaPackApplicability:
    pack_id: str
    supported_profile_ids: tuple[str, ...]
    supported_asset_classes: tuple[str, ...]
    supported_subtypes: tuple[str, ...]
    allowed_label_ids: tuple[str, ...]
    required_fields: tuple[str, ...]
    required_release_components: tuple[str, ...]
    minimum_lookback_sessions: int
    pit_required: bool
    missing_policy: str
    required_dataset_kinds: tuple[str, ...] = ()

    @property
    def fingerprint(self) -> str:
        return sha256_json(asdict(self))


_PROFILE_RULES: Mapping[
    str,
    tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]],
] = {
    "qlib_alpha158_official_v1": (
        ("ashare_equity_v1",),
        ("equity",),
        ("common_stock",),
        ("return_5d_t1_v1",),
        (),
    ),
    "alpha158_market_v1": (
        ("ashare_equity_v1",),
        ("equity",),
        ("common_stock",),
        ("return_5d_t1_v1",),
        (),
    ),
    "alpha158_daily_v1": (
        ("ashare_equity_v1",),
        ("equity",),
        ("common_stock",),
        ("return_5d_t1_v1",),
        (),
    ),
    "alpha158_pit_v1": (
        ("ashare_equity_v1",),
        ("equity",),
        ("common_stock",),
        ("return_5d_t1_v1",),
        (),
    ),
    "multifactor_core_v1": (
        ("ashare_equity_v1",),
        ("equity",),
        ("common_stock",),
        ("return_5d_t1_v1",),
        (),
    ),
    "ashare_factor_benchmark_v1": (
        ("ashare_equity_v1",),
        ("equity",),
        ("common_stock",),
        ("return_5d_t1_v1",),
        (),
    ),
    "ashare_alpha_phase2_v1": (
        ("ashare_equity_v1",),
        ("equity",),
        ("common_stock",),
        ("return_5d_t1_v1",),
        (),
    ),
    "etf_core_v1": (
        ("ashare_etf_v1",),
        ("equity",),
        ("etf",),
        ("return_5d_t1_v1",),
        ETF_REQUIRED_DATASETS,
    ),
}


def alpha_pack_applicability(pack: AlphaPackSpec) -> AlphaPackApplicability:
    try:
        profiles, asset_classes, subtypes, allowed_labels, dataset_kinds = _PROFILE_RULES[pack.pack_id]
    except KeyError as exc:
        raise ValueError(
            f"alpha pack has no ResearchProfile applicability descriptor: {pack.pack_id}"
        ) from exc
    return AlphaPackApplicability(
        pack_id=pack.pack_id,
        supported_profile_ids=profiles,
        supported_asset_classes=asset_classes,
        supported_subtypes=subtypes,
        allowed_label_ids=allowed_labels,
        required_fields=pack.required_qlib_fields,
        required_release_components=pack.required_release_components,
        minimum_lookback_sessions=pack.warmup_trading_days,
        pit_required="pit_fundamentals" in pack.required_release_components,
        missing_policy="reject_required_fields",
        required_dataset_kinds=dataset_kinds,
    )


def assert_alpha_pack_profile_compatible(pack: AlphaPackSpec, profile: ResearchProfile) -> None:
    descriptor = alpha_pack_applicability(pack)
    if profile.profile_id not in descriptor.supported_profile_ids:
        raise ValueError(
            f"alpha pack {pack.pack_id} is not compatible with research profile {profile.profile_id}"
        )
    if profile.asset_class not in descriptor.supported_asset_classes:
        raise ValueError(f"alpha pack {pack.pack_id} does not support asset class {profile.asset_class!r}")
    if profile.subtype not in descriptor.supported_subtypes:
        raise ValueError(f"alpha pack {pack.pack_id} does not support subtype {profile.subtype!r}")
    if profile.label.label_id not in descriptor.allowed_label_ids:
        raise ValueError(f"alpha pack {pack.pack_id} does not support label {profile.label.label_id!r}")
