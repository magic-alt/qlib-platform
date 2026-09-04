from __future__ import annotations

from dataclasses import asdict, dataclass
import numpy as np
import pandas as pd

from qlib_platform.lineage import sha256_json


BENCHMARK_FAMILIES: dict[str, tuple[str, ...]] = {
    "Value": ("EARNINGS_YIELD", "BOOK_TO_PRICE", "DIVIDEND_YIELD"),
    "Profitability": (
        "GROSS_PROFIT_ASSETS",
        "OPERATING_PROFIT_ASSETS",
        "CASHFLOW_PROFIT_ASSETS",
        "ROE_PIT",
        "ROA_PIT",
    ),
    "Growth": (
        "REVENUE_GROWTH_TTM",
        "EARNINGS_GROWTH_TTM",
        "OPERATING_PROFIT_GROWTH_TTM",
        "CASHFLOW_GROWTH_TTM",
    ),
    "Investment": ("ASSET_GROWTH", "CAPEX_ASSETS"),
    "Accruals": ("ACCRUALS",),
    "LowRisk": ("VOL_20", "DOWNSIDE_VOL_20"),
    "Size": ("LOG_TOTAL_MV",),
    "Liquidity": ("TURNOVER_20", "AMIHUD_20"),
    "PriceMomentum": ("MOMENTUM_6M", "MOMENTUM_12M"),
    "Reversal": ("REVERSAL_5D",),
}

ORIENTATIONS: dict[str, float] = {
    **{
        name: 1.0
        for family in ("Value", "Profitability", "Growth", "PriceMomentum")
        for name in BENCHMARK_FAMILIES[family]
    },
    "ASSET_GROWTH": -1.0,
    "CAPEX_ASSETS": -1.0,
    "ACCRUALS": -1.0,
    "VOL_20": -1.0,
    "DOWNSIDE_VOL_20": -1.0,
    "LOG_TOTAL_MV": 1.0,
    "TURNOVER_20": -1.0,
    "AMIHUD_20": -1.0,
    "REVERSAL_5D": -1.0,
}


@dataclass(frozen=True)
class FeatureSetSpec:
    feature_set_id: str
    source_pack: str
    families: tuple[str, ...]
    include_selected_technical: bool = False
    include_interactions: bool = False

    @property
    def fingerprint(self) -> str:
        return sha256_json(asdict(self))

    def to_manifest(self) -> dict[str, object]:
        return {**asdict(self), "featureSetSha256": self.fingerprint}


@dataclass(frozen=True)
class HypothesisFeatureSetSpec:
    feature_set_id: str
    hypothesis_id: str
    role: str
    features: tuple[str, ...]
    embedded_controls: tuple[str, ...] = ()
    source_pack: str = "ashare_alpha_phase2_v1"

    @property
    def fingerprint(self) -> str:
        return sha256_json(asdict(self))

    def to_manifest(self) -> dict[str, object]:
        return {**asdict(self), "featureSetSha256": self.fingerprint}

    @property
    def families(self) -> tuple[str, ...]:
        return ()

    @property
    def include_selected_technical(self) -> bool:
        return False

    @property
    def include_interactions(self) -> bool:
        return False


FEATURE_SETS: dict[str, FeatureSetSpec] = {
    "A0": FeatureSetSpec("A0", "alpha158_pit_v1", ("Alpha158",)),
    "A1": FeatureSetSpec("A1", "ashare_alpha_phase2_v1", ("Value",)),
    "A2": FeatureSetSpec("A2", "ashare_alpha_phase2_v1", ("LowRisk",)),
    "A3": FeatureSetSpec("A3", "ashare_alpha_phase2_v1", ("Value", "LowRisk")),
    "A4": FeatureSetSpec("A4", "ashare_alpha_phase2_v1", ("Value", "LowRisk", "Profitability")),
    "A5": FeatureSetSpec(
        "A5",
        "ashare_alpha_phase2_v1",
        ("Value", "LowRisk", "Profitability", "Growth", "FundamentalMomentum"),
    ),
    "A6": FeatureSetSpec(
        "A6",
        "ashare_alpha_phase2_v1",
        ("Value", "LowRisk", "Profitability", "Growth", "FundamentalMomentum", "Liquidity"),
    ),
    "A7": FeatureSetSpec(
        "A7",
        "ashare_alpha_phase2_v1",
        ("Value", "LowRisk", "Profitability", "Growth", "FundamentalMomentum", "Liquidity"),
        include_selected_technical=True,
    ),
    "VP1": FeatureSetSpec("VP1", "ashare_alpha_phase2_v1", ("Value", "Profitability")),
    "LVR1": FeatureSetSpec("LVR1", "ashare_alpha_phase2_v1", ("ResidualLowRisk",)),
    "I1": FeatureSetSpec(
        "I1",
        "ashare_alpha_phase2_v1",
        ("Value", "LowRisk", "Profitability", "PriceMomentum", "Liquidity"),
        include_interactions=True,
    ),
}


def _hypothesis_pair(
    hypothesis_id: str,
    baseline: tuple[str, ...],
    added: str,
    *,
    embedded_controls: tuple[str, ...] = (),
) -> tuple[HypothesisFeatureSetSpec, HypothesisFeatureSetSpec]:
    return (
        HypothesisFeatureSetSpec(
            f"{hypothesis_id}_BASELINE",
            hypothesis_id,
            "baseline",
            baseline,
            embedded_controls,
        ),
        HypothesisFeatureSetSpec(
            f"{hypothesis_id}_CANDIDATE",
            hypothesis_id,
            "candidate",
            (*baseline, added),
            embedded_controls,
        ),
    )


_HYPOTHESIS_PAIRS = (
    _hypothesis_pair(
        "H001",
        (
            "SIZE_COMPOSITE",
            "PROFITABILITY_COMPOSITE",
            "LOWVOL_COMPOSITE",
            "LIQUIDITY_COMPOSITE",
        ),
        "EARNINGS_YIELD",
    ),
    _hypothesis_pair(
        "H002",
        ("SIZE_COMPOSITE", "VALUE_COMPOSITE", "PROFITABILITY_COMPOSITE"),
        "LOWVOL_RESIDUAL",
        embedded_controls=("INDUSTRY_L1_CODE",),
    ),
    _hypothesis_pair(
        "H003",
        ("SIZE_COMPOSITE", "VALUE_COMPOSITE", "LOWVOL_COMPOSITE", "LIQUIDITY_COMPOSITE"),
        "GROSS_PROFIT_ASSETS",
    ),
    _hypothesis_pair(
        "H004",
        ("SIZE_COMPOSITE", "VALUE_COMPOSITE", "LOWVOL_COMPOSITE", "LIQUIDITY_COMPOSITE"),
        "OPERATING_PROFIT_ASSETS",
    ),
    _hypothesis_pair(
        "H005",
        (
            "SIZE_COMPOSITE",
            "VALUE_COMPOSITE",
            "PROFITABILITY_COMPOSITE",
            "LOWVOL_COMPOSITE",
            "LIQUIDITY_COMPOSITE",
        ),
        "FUNDAMENTAL_MOMENTUM",
    ),
    _hypothesis_pair(
        "H101",
        ("VALUE_COMPOSITE", "VOLATILITY_COMPOSITE"),
        "VALUE_X_VOLATILITY",
    ),
    _hypothesis_pair(
        "H102",
        ("VALUE_COMPOSITE", "SIZE_COMPOSITE"),
        "VALUE_X_SIZE",
    ),
    _hypothesis_pair(
        "H103",
        ("PROFITABILITY_COMPOSITE", "VALUE_COMPOSITE"),
        "PROFITABILITY_X_VALUE",
    ),
    _hypothesis_pair(
        "H104",
        ("PROFITABILITY_COMPOSITE", "LOWVOL_COMPOSITE"),
        "PROFITABILITY_X_LOWVOL",
    ),
    _hypothesis_pair(
        "H105",
        ("MOMENTUM_COMPOSITE", "VOLATILITY_COMPOSITE"),
        "MOMENTUM_X_VOLATILITY",
    ),
    _hypothesis_pair(
        "H106",
        ("LIQUIDITY_COMPOSITE", "VOLATILITY_COMPOSITE"),
        "LIQUIDITY_X_VOLATILITY",
    ),
)

HYPOTHESIS_FEATURE_SETS: dict[str, HypothesisFeatureSetSpec] = {
    spec.feature_set_id: spec for pair in _HYPOTHESIS_PAIRS for spec in pair
}

EXPERIMENT_MATRIX: dict[str, tuple[str, str]] = {
    "P2-01": ("A1", "ridge"),
    "P2-02": ("A2", "ridge"),
    "P2-03": ("A3", "ridge"),
    "P2-04": ("A3", "xgboost"),
    "P2-05": ("VP1", "ridge"),
    "P2-06": ("A4", "ridge"),
    "P2-07": ("A4", "xgboost"),
    "P2-08": ("A5", "xgboost"),
    "P2-09": ("A7", "ridge"),
    "P2-10": ("A7", "xgboost"),
}

INTERACTIONS: dict[str, tuple[str, str]] = {
    "VALUE_X_VOLATILITY": ("VALUE_COMPOSITE", "VOLATILITY_COMPOSITE"),
    "VALUE_X_SIZE": ("VALUE_COMPOSITE", "SIZE_COMPOSITE"),
    "PROFITABILITY_X_VALUE": ("PROFITABILITY_COMPOSITE", "VALUE_COMPOSITE"),
    "PROFITABILITY_X_LOWVOL": ("PROFITABILITY_COMPOSITE", "LOWVOL_COMPOSITE"),
    "MOMENTUM_X_VOLATILITY": ("MOMENTUM_COMPOSITE", "VOLATILITY_COMPOSITE"),
    "LIQUIDITY_X_VOLATILITY": ("LIQUIDITY_COMPOSITE", "VOLATILITY_COMPOSITE"),
}


def feature_set(feature_set_id: str) -> FeatureSetSpec | HypothesisFeatureSetSpec:
    try:
        return FEATURE_SETS.get(feature_set_id) or HYPOTHESIS_FEATURE_SETS[feature_set_id]
    except KeyError as exc:
        raise ValueError(f"unknown Phase 2 feature set: {feature_set_id}") from exc


def _safe_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    top = pd.to_numeric(numerator, errors="coerce")
    bottom = pd.to_numeric(denominator, errors="coerce")
    return top.div(bottom.where(bottom.abs().gt(1e-12))).replace([np.inf, -np.inf], np.nan)


def _daily_rank_z(values: pd.Series) -> pd.Series:
    def transform(block: pd.Series) -> pd.Series:
        ranked = block.rank(method="average", pct=True)
        scale = ranked.std(ddof=0)
        return (
            ranked - ranked.mean()
            if not np.isfinite(scale) or scale <= 1e-12
            else (ranked - ranked.mean()) / scale
        )

    return values.groupby(level="datetime", sort=True, group_keys=False).apply(transform)


def _composite(frame: pd.DataFrame, names: tuple[str, ...]) -> pd.Series:
    oriented = [_daily_rank_z(frame[name] * ORIENTATIONS[name]).rename(name) for name in names]
    return pd.concat(oriented, axis=1).mean(axis=1, skipna=False)


def _rolling_by_instrument(values: pd.Series, window: int, operation: str) -> pd.Series:
    grouped = values.groupby(level="instrument", sort=False)
    if operation == "sum":
        result = grouped.rolling(window, min_periods=window).sum()
    elif operation == "std":
        result = grouped.rolling(window, min_periods=window).std(ddof=1)
    else:
        raise ValueError(f"unsupported rolling operation: {operation}")
    return result.droplevel(0).reindex(values.index)


def build_benchmark_factors(
    raw: pd.DataFrame,
    *,
    non_applicable_industry_codes: set[str] | None = None,
) -> pd.DataFrame:
    """Build deterministic benchmark characteristics from PIT data facts.

    The input uses a datetime/instrument MultiIndex and lower-case DataRelease
    field names.  Financial-industry exclusions affect only accounting ratios;
    excluded rows stay in the universe and are represented as missing values.
    """

    if not isinstance(raw.index, pd.MultiIndex) or raw.index.names != ["datetime", "instrument"]:
        raise ValueError("Phase 2 factors require a datetime/instrument MultiIndex")
    required = {
        "close",
        "money",
        "turnover_rate_f",
        "total_mv",
        "dv_ttm",
        "industry_l1_code",
        "roe_waa_pit",
        "roa_pit",
        "total_assets_pit",
        "prior_year_total_assets_pit",
        "total_equity_pit",
        "gross_profit_ttm_pit",
        "operating_profit_ttm_pit",
        "prior_year_operating_profit_ttm_pit",
        "operating_cash_flow_ttm_pit",
        "prior_year_operating_cash_flow_ttm_pit",
        "revenue_ttm_pit",
        "prior_year_revenue_ttm_pit",
        "parent_net_income_ttm_pit",
        "prior_year_parent_net_income_ttm_pit",
        "capex_ttm_pit",
    }
    missing = required - set(raw)
    if missing:
        raise ValueError(f"Phase 2 benchmark input is missing fields: {sorted(missing)}")
    numeric = raw.copy()
    for name in required - {"industry_l1_code"}:
        numeric[name] = pd.to_numeric(numeric[name], errors="coerce")
    average_assets = (numeric["total_assets_pit"] + numeric["prior_year_total_assets_pit"]) / 2.0
    output = pd.DataFrame(index=numeric.index)
    output["EARNINGS_YIELD"] = _safe_ratio(numeric["parent_net_income_ttm_pit"], numeric["total_mv"])
    output["BOOK_TO_PRICE"] = _safe_ratio(numeric["total_equity_pit"], numeric["total_mv"])
    output["DIVIDEND_YIELD"] = numeric["dv_ttm"]
    output["GROSS_PROFIT_ASSETS"] = _safe_ratio(numeric["gross_profit_ttm_pit"], average_assets)
    output["OPERATING_PROFIT_ASSETS"] = _safe_ratio(numeric["operating_profit_ttm_pit"], average_assets)
    output["CASHFLOW_PROFIT_ASSETS"] = _safe_ratio(numeric["operating_cash_flow_ttm_pit"], average_assets)
    output["ROE_PIT"] = numeric["roe_waa_pit"]
    output["ROA_PIT"] = numeric["roa_pit"]
    output["REVENUE_GROWTH_TTM"] = (
        _safe_ratio(numeric["revenue_ttm_pit"], numeric["prior_year_revenue_ttm_pit"]) - 1.0
    )
    output["EARNINGS_GROWTH_TTM"] = (
        _safe_ratio(numeric["parent_net_income_ttm_pit"], numeric["prior_year_parent_net_income_ttm_pit"])
        - 1.0
    )
    output["OPERATING_PROFIT_GROWTH_TTM"] = (
        _safe_ratio(
            numeric["operating_profit_ttm_pit"],
            numeric["prior_year_operating_profit_ttm_pit"],
        )
        - 1.0
    )
    output["CASHFLOW_GROWTH_TTM"] = (
        _safe_ratio(
            numeric["operating_cash_flow_ttm_pit"],
            numeric["prior_year_operating_cash_flow_ttm_pit"],
        )
        - 1.0
    )
    output["ASSET_GROWTH"] = (
        _safe_ratio(numeric["total_assets_pit"], numeric["prior_year_total_assets_pit"]) - 1.0
    )
    output["CAPEX_ASSETS"] = _safe_ratio(numeric["capex_ttm_pit"], average_assets)
    output["ACCRUALS"] = _safe_ratio(
        numeric["parent_net_income_ttm_pit"] - numeric["operating_cash_flow_ttm_pit"],
        average_assets,
    )
    close = numeric["close"]
    returns = close.groupby(level="instrument", sort=False).pct_change(fill_method=None)
    output["VOL_20"] = _rolling_by_instrument(returns, 20, "std")
    output["DOWNSIDE_VOL_20"] = _rolling_by_instrument(returns.where(returns.lt(0), 0.0), 20, "std")
    output["LOG_TOTAL_MV"] = np.log(numeric["total_mv"].where(numeric["total_mv"].gt(0)))
    output["TURNOVER_20"] = (
        numeric["turnover_rate_f"]
        .groupby(level="instrument", sort=False)
        .rolling(20, min_periods=20)
        .mean()
        .droplevel(0)
        .reindex(numeric.index)
    )
    output["AMIHUD_20"] = _rolling_by_instrument(returns.abs().div(numeric["money"] + 1.0), 20, "sum") / 20.0
    output["MOMENTUM_6M"] = close.groupby(level="instrument", sort=False).pct_change(126, fill_method=None)
    output["MOMENTUM_12M"] = close.groupby(level="instrument", sort=False).pct_change(252, fill_method=None)
    output["REVERSAL_5D"] = close.groupby(level="instrument", sort=False).pct_change(5, fill_method=None)

    excluded = {str(value) for value in (non_applicable_industry_codes or set())}
    if excluded:
        applicable = ~raw["industry_l1_code"].astype(str).isin(excluded)
        accounting = (
            set(BENCHMARK_FAMILIES["Profitability"])
            | set(BENCHMARK_FAMILIES["Investment"])
            | set(BENCHMARK_FAMILIES["Accruals"])
        )
        output.loc[~applicable, sorted(accounting)] = np.nan

    output["VALUE_COMPOSITE"] = _composite(output, BENCHMARK_FAMILIES["Value"])
    output["PROFITABILITY_COMPOSITE"] = _composite(output, BENCHMARK_FAMILIES["Profitability"])
    output["LOWVOL_COMPOSITE"] = _composite(output, BENCHMARK_FAMILIES["LowRisk"])
    output["VOLATILITY_COMPOSITE"] = -output["LOWVOL_COMPOSITE"]
    output["SIZE_COMPOSITE"] = _daily_rank_z(output["LOG_TOTAL_MV"])
    output["LIQUIDITY_COMPOSITE"] = _composite(output, BENCHMARK_FAMILIES["Liquidity"])
    output["MOMENTUM_COMPOSITE"] = _composite(output, BENCHMARK_FAMILIES["PriceMomentum"])
    output["FUNDAMENTAL_MOMENTUM"] = pd.concat(
        [
            _daily_rank_z(output["REVENUE_GROWTH_TTM"]),
            _daily_rank_z(output["EARNINGS_GROWTH_TTM"]),
            _daily_rank_z(output["OPERATING_PROFIT_GROWTH_TTM"]),
            _daily_rank_z(output["CASHFLOW_GROWTH_TTM"]),
        ],
        axis=1,
    ).mean(axis=1, skipna=False)
    return output.sort_index()


def build_explicit_interactions(composites: pd.DataFrame) -> pd.DataFrame:
    missing = sorted({name for pair in INTERACTIONS.values() for name in pair} - set(composites))
    if missing:
        raise ValueError(f"interaction inputs are missing: {missing}")
    output = pd.DataFrame(index=composites.index)
    for name, (left, right) in INTERACTIONS.items():
        product = _daily_rank_z(composites[left]) * _daily_rank_z(composites[right])
        output[name] = _daily_rank_z(product)
    return output.sort_index()


def residualize_lowvol(
    composites: pd.DataFrame,
    industry_codes: pd.Series,
    *,
    minimum_cross_section: int = 50,
) -> pd.Series:
    required = {"LOWVOL_COMPOSITE", "VALUE_COMPOSITE", "PROFITABILITY_COMPOSITE", "SIZE_COMPOSITE"}
    if missing := required - set(composites):
        raise ValueError(f"LowVol residualization inputs are missing: {sorted(missing)}")
    if not composites.index.equals(industry_codes.index):
        raise ValueError("LowVol composites and PIT industries must align exactly")
    residual = pd.Series(float("nan"), index=composites.index, dtype=float)
    for date, block in composites.groupby(level="datetime", sort=True):
        industries = industry_codes.xs(date, level="datetime").reindex(
            block.index.get_level_values("instrument")
        )
        controls = block[["VALUE_COMPOSITE", "PROFITABILITY_COMPOSITE", "SIZE_COMPOSITE"]].copy()
        controls.index = controls.index.get_level_values("instrument")
        target = block["LOWVOL_COMPOSITE"].copy()
        target.index = target.index.get_level_values("instrument")
        dummies = pd.get_dummies(industries.astype("string"), prefix="industry", dtype=float)
        design = pd.concat([controls, dummies.iloc[:, 1:]], axis=1)
        joined = pd.concat([target.rename("target"), design], axis=1).dropna()
        if len(joined) < minimum_cross_section:
            continue
        matrix = np.column_stack([np.ones(len(joined)), joined.drop(columns="target").to_numpy(dtype=float)])
        coefficients = np.linalg.pinv(matrix) @ joined["target"].to_numpy(dtype=float)
        values = joined["target"].to_numpy(dtype=float) - matrix @ coefficients
        keys = pd.MultiIndex.from_product([[pd.Timestamp(date)], joined.index], names=composites.index.names)
        residual.loc[keys] = values
    result = _daily_rank_z(residual)
    result.name = "LOWVOL_RESIDUAL_COMPOSITE"
    return result


def select_cluster_representative(summary: pd.DataFrame) -> str:
    required = {"feature", "gate_pass", "oriented_rank_ic", "turnover", "coverage"}
    if missing := required - set(summary):
        raise ValueError(f"cluster summary is missing columns: {sorted(missing)}")
    eligible = summary.loc[summary["gate_pass"].eq(True)].copy()
    if eligible.empty:
        raise ValueError("cluster has no feature that passes the pre-registered gate")
    eligible = eligible.sort_values(
        ["oriented_rank_ic", "turnover", "coverage", "feature"],
        ascending=[False, True, False, True],
        kind="stable",
    )
    return str(eligible.iloc[0]["feature"])
