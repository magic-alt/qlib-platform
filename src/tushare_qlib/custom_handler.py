from __future__ import annotations

from qlib.contrib.data.handler import Alpha158

from .fundamentals import PIT_FIELDS


PIT_FEATURE_EXPRESSIONS = tuple(f"${field}" for field in PIT_FIELDS)
PIT_FEATURE_NAMES = (
    "ROE_WAA_PIT",
    "ROA_PIT",
    "NET_MARGIN_PIT",
    "NETPROFIT_YOY_PIT",
    "REVENUE_YOY_PIT",
    "DEBT_ASSETS_PIT",
    "OCF_OR_PIT",
)


class TushareAlpha158Daily(Alpha158):
    """Alpha158 plus valuation, liquidity, money-flow and A-share state fields."""

    def get_feature_config(self):
        fields, names = super().get_feature_config()
        custom_fields = [
            "$turnover_rate_f",
            "$volume_ratio",
            "Log($circ_mv + 1)",
            "$circ_mv",
            "$pe_ttm",
            "$pb",
            "$ps_ttm",
            "$dv_ttm",
            "Mean($money, 20)",
            "Mean($net_mf_amount, 5) / (Mean($money, 5) + 1)",
            "Mean($net_mf_amount, 20) / (Mean($money, 20) + 1)",
            "Mean($big_net_amount, 5) / (Mean($money, 5) + 1)",
            "$paused",
            "$is_st",
            "$listed_days",
            "$is_limit_up",
            "$is_limit_down",
        ]
        custom_names = [
            "TURNOVER_F", "VOLUME_RATIO", "LOG_CIRC_MV", "CIRC_MV", "PE_TTM", "PB", "PS_TTM", "DV_TTM",
            "MONEY20", "NET_MF_5", "NET_MF_20", "BIG_NET_5", "PAUSED", "IS_ST", "LISTED_DAYS",
            "IS_LIMIT_UP", "IS_LIMIT_DOWN",
        ]
        return list(fields) + custom_fields, list(names) + custom_names


class TushareAlpha158Fundamental(TushareAlpha158Daily):
    """Extension for daily-expanded point-in-time financial fields."""

    def get_feature_config(self):
        fields, names = super().get_feature_config()
        return list(fields) + list(PIT_FEATURE_EXPRESSIONS), list(names) + list(PIT_FEATURE_NAMES)
