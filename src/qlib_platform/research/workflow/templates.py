from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ResearchTemplate:
    template_id: str
    description: str
    alpha_pack: str
    model_name: str
    model_profile: str
    overlay: Mapping[str, Any]
    parity_notes: tuple[str, ...] = ()

    def to_catalog(self) -> dict[str, object]:
        return {
            "id": self.template_id,
            "description": self.description,
            "alphaPack": self.alpha_pack,
            "model": self.model_name,
            "modelProfile": self.model_profile,
            "parityNotes": list(self.parity_notes),
        }

    def config_overlay(self) -> dict[str, Any]:
        return deepcopy(dict(self.overlay))


RESEARCH_TEMPLATES: dict[str, ResearchTemplate] = {
    "platform_alpha158_market_v1": ResearchTemplate(
        template_id="platform_alpha158_market_v1",
        description="qlib-platform production-style Alpha158 market baseline",
        alpha_pack="alpha158_market_v1",
        model_name="lightgbm",
        model_profile="configs/model_profiles/lightgbm_auto.yaml",
        overlay={},
        parity_notes=(
            "Uses the platform production preprocessing, execution and research-gate contract.",
        ),
    ),
    "qlib_alpha158_official_v1": ResearchTemplate(
        template_id="qlib_alpha158_official_v1",
        description="Microsoft Qlib Alpha158 LightGBM reference protocol on the pinned local DatasetVersion",
        alpha_pack="qlib_alpha158_official_v1",
        model_name="lightgbm_qlib_alpha158_official_v1",
        model_profile="configs/model_profiles/lightgbm_qlib_alpha158_official_v1.yaml",
        overlay={
            "experiment": {"label": {"spec": "return_1d_t1_v1"}},
            "research": {
                "label_horizon_days": 1,
                "signal_lag_days": 1,
                "backtest_account": 100_000_000,
                "deal_price": "close",
                "limit_threshold": 0.095,
                "open_cost": 0.0005,
                "close_cost": 0.0015,
                "min_cost": 5,
                # The platform keeps a finite, local-data execution guard instead
                # of removing the volume cap entirely.  One hundred percent of
                # observed daily volume is effectively non-binding for this control.
                "max_participation_rate": 1.0,
                "benchmark": "SH000300",
            },
            "strategy": {
                "policy": "topk_dropout_v1",
                "topk_dropout": {
                    "topk": 50,
                    "n_drop": 5,
                    "hold_thresh": 1,
                    "only_tradable": False,
                    "forbid_all_trade_at_limit": True,
                    "risk_degree": 0.95,
                },
            },
            "universe": {
                "instruments": "csi300",
                "label": "Qlib official Alpha158 local CSI300",
                "min_listed_days": 0,
                "min_circ_mv_yuan": 0,
                "min_money_20d_yuan": 0,
                "exclude_st": False,
                "allow_unknown_st": True,
            },
        },
        parity_notes=(
            "Alpha158 feature expressions and processors match the upstream reference handler.",
            "The label is the upstream one-day T+1 close-return label.",
            "LightGBM, TopK50/drop5/hold1, account, close execution and fee parameters mirror the reference recipe.",
            "Actual stocks, prices, membership history and benchmark observations come from the pinned local DatasetVersion.",
            "Local research dates are used instead of forcing the upstream 2008-2020 sample onto a different dataset.",
            "Point-in-time local limit flags and the platform volume guard remain enabled for deterministic executable replay.",
        ),
    ),
}


def get_research_template(template_id: str | None) -> ResearchTemplate | None:
    if not template_id:
        return None
    try:
        return RESEARCH_TEMPLATES[template_id]
    except KeyError as exc:
        raise ValueError(f"unknown research template: {template_id}") from exc


def template_catalog() -> list[dict[str, object]]:
    return [RESEARCH_TEMPLATES[key].to_catalog() for key in sorted(RESEARCH_TEMPLATES)]


def deep_merge(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(base))
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result
