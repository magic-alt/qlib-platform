from __future__ import annotations

from qlib.contrib.data.handler import Alpha158

from qlib_platform.data.fundamentals import PIT_FIELDS


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


class TushareAlpha158Market(Alpha158):
    """Price/volume-only Alpha158 handler for exploratory market imports."""


class TushareAlpha158Daily(TushareAlpha158Market):
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
            "TURNOVER_F",
            "VOLUME_RATIO",
            "LOG_CIRC_MV",
            "CIRC_MV",
            "PE_TTM",
            "PB",
            "PS_TTM",
            "DV_TTM",
            "MONEY20",
            "NET_MF_5",
            "NET_MF_20",
            "BIG_NET_5",
            "PAUSED",
            "IS_ST",
            "LISTED_DAYS",
            "IS_LIMIT_UP",
            "IS_LIMIT_DOWN",
        ]
        return list(fields) + custom_fields, list(names) + custom_names


class TushareAlpha158Fundamental(TushareAlpha158Daily):
    """Extension for daily-expanded point-in-time financial fields."""

    def get_feature_config(self):
        fields, names = super().get_feature_config()
        return list(fields) + list(PIT_FEATURE_EXPRESSIONS), list(names) + list(PIT_FEATURE_NAMES)


class TushareMultiFactorCore(Alpha158):
    """Causal style-factor panel migrated from the retired platform ML recipe."""

    def get_feature_config(self):
        daily_return = "$close/Ref($close, 1)-1"
        fields = [
            "$close/Ref($close, 1)-1",
            "$close/Ref($close, 5)-1",
            "$close/Ref($close, 10)-1",
            "$close/Ref($close, 20)-1",
            "$close/Ref($close, 60)-1",
            "$close/Mean($close, 5)-1",
            "$close/Mean($close, 20)-1",
            "$close/Mean($close, 60)-1",
            "Mean($close, 5)/Mean($close, 20)-1",
            "Mean($close, 20)/Mean($close, 60)-1",
            f"Std({daily_return}, 5)",
            f"Std({daily_return}, 20)",
            f"Std({daily_return}, 60)",
            "Mean($high-$low, 14)",
            "$close/Max($close, 20)-1",
            f"Std(If({daily_return}<0, {daily_return}, 0), 20)",
            "Log(Mean($money, 20)+1)",
            "$money/(Mean($money, 20)+1)",
            "Mean($turnover_rate_f, 5)",
            f"Mean(Abs({daily_return})/($money+1), 20)",
            "$turnover_rate_f",
            "$volume_ratio",
            "If($pe_ttm>0, 1/$pe_ttm, 0)",
            "If($pb>0, 1/$pb, 0)",
            "If($ps_ttm>0, 1/$ps_ttm, 0)",
            "Log($circ_mv+1)",
            *PIT_FEATURE_EXPRESSIONS,
            "$industry_l1_code",
            "$paused",
            "$is_st",
            "$listed_days",
            "$circ_mv",
            "Mean($money, 20)",
        ]
        names = [
            "RET_1",
            "RET_5",
            "RET_10",
            "RET_20",
            "RET_60",
            "CLOSE_TO_MA_5",
            "CLOSE_TO_MA_20",
            "CLOSE_TO_MA_60",
            "MA5_TO_MA20",
            "MA20_TO_MA60",
            "VOL_5",
            "VOL_20",
            "VOL_60",
            "ATR_14",
            "DRAWDOWN_20",
            "DOWNSIDE_VOL_20",
            "LOG_MONEY_20",
            "MONEY_RATIO_20",
            "TURNOVER_MEAN_5",
            "AMIHUD_20",
            "TURNOVER_F",
            "VOLUME_RATIO",
            "EARNINGS_YIELD_TTM",
            "BOOK_TO_PRICE",
            "SALES_TO_PRICE_TTM",
            "LOG_CIRC_MV",
            *PIT_FEATURE_NAMES,
            "INDUSTRY_L1_CODE",
            "PAUSED",
            "IS_ST",
            "LISTED_DAYS",
            "CIRC_MV",
            "MONEY20",
        ]
        return fields, names


class TushareAshareFactorBenchmark(Alpha158):
    """Pre-registered China A-share benchmark characteristics.

    Accounting inputs are already point-in-time/TTM standardized by the
    DataRelease.  This handler owns only economically named ratios and price
    history expressions.
    """

    def get_feature_config(self):
        daily_return = "$close/Ref($close, 1)-1"
        average_assets = "($total_assets_pit+$prior_year_total_assets_pit)/2"
        fields = [
            "$parent_net_income_ttm_pit/$total_mv",
            "$total_equity_pit/$total_mv",
            "$dv_ttm",
            f"$gross_profit_ttm_pit/{average_assets}",
            f"$operating_profit_ttm_pit/{average_assets}",
            f"$operating_cash_flow_ttm_pit/{average_assets}",
            "$roe_waa_pit",
            "$roa_pit",
            "$revenue_ttm_pit/$prior_year_revenue_ttm_pit-1",
            "$parent_net_income_ttm_pit/$prior_year_parent_net_income_ttm_pit-1",
            "$operating_profit_ttm_pit/$prior_year_operating_profit_ttm_pit-1",
            "$operating_cash_flow_ttm_pit/$prior_year_operating_cash_flow_ttm_pit-1",
            "$total_assets_pit/$prior_year_total_assets_pit-1",
            f"$capex_ttm_pit/{average_assets}",
            f"($parent_net_income_ttm_pit-$operating_cash_flow_ttm_pit)/{average_assets}",
            f"Std({daily_return},20)",
            f"Std(If({daily_return}<0,{daily_return},0),20)",
            "Log($total_mv+1)",
            "Log($circ_mv+1)",
            "Mean($turnover_rate_f,20)",
            f"Mean(Abs({daily_return})/($money+1),20)",
            "$close/Ref($close,126)-1",
            "$close/Ref($close,252)-1",
            "$close/Ref($close,5)-1",
            "$industry_l1_code",
            "$paused",
            "$is_st",
            "$listed_days",
            "$circ_mv",
            "Mean($money,20)",
        ]
        names = [
            "EARNINGS_YIELD",
            "BOOK_TO_PRICE",
            "DIVIDEND_YIELD",
            "GROSS_PROFIT_ASSETS",
            "OPERATING_PROFIT_ASSETS",
            "CASHFLOW_PROFIT_ASSETS",
            "ROE_PIT",
            "ROA_PIT",
            "REVENUE_GROWTH_TTM",
            "EARNINGS_GROWTH_TTM",
            "OPERATING_PROFIT_GROWTH_TTM",
            "CASHFLOW_GROWTH_TTM",
            "ASSET_GROWTH",
            "CAPEX_ASSETS",
            "ACCRUALS",
            "VOL_20",
            "DOWNSIDE_VOL_20",
            "LOG_TOTAL_MV",
            "LOG_CIRC_MV",
            "TURNOVER_20",
            "AMIHUD_20",
            "MOMENTUM_6M",
            "MOMENTUM_12M",
            "REVERSAL_5D",
            "INDUSTRY_L1_CODE",
            "PAUSED",
            "IS_ST",
            "LISTED_DAYS",
            "CIRC_MV",
            "MONEY20",
        ]
        return fields, names


class TushareAsharePhase2(TushareAshareFactorBenchmark):
    """Phase 2 superset; immutable feature subsets own individual ablations."""
