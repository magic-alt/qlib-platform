from __future__ import annotations

import numpy as np
import pandas as pd
from qlib.data.dataset.processor import Processor

from .research.phase2_features import BENCHMARK_FAMILIES, INTERACTIONS, ORIENTATIONS, feature_set


class AshareUniverseFilter(Processor):
    """Point-in-time row filter executed before model normalization."""

    def __init__(
        self,
        min_listed_days: int = 120,
        min_circ_mv_yuan: float = 2_000_000_000,
        min_money_20d_yuan: float = 20_000_000,
        exclude_st: bool = True,
        allow_unknown_st: bool = True,
    ) -> None:
        self.min_listed_days = min_listed_days
        self.min_circ_mv_yuan = min_circ_mv_yuan
        self.min_money_20d_yuan = min_money_20d_yuan
        self.exclude_st = exclude_st
        self.allow_unknown_st = allow_unknown_st

    @staticmethod
    def _feature(df: pd.DataFrame, name: str) -> pd.Series:
        if isinstance(df.columns, pd.MultiIndex):
            return df[("feature", name)]
        return df[name]

    def __call__(self, df: pd.DataFrame) -> pd.DataFrame:
        paused = self._feature(df, "PAUSED")
        listed = self._feature(df, "LISTED_DAYS")
        circ_mv = self._feature(df, "CIRC_MV")
        money20 = self._feature(df, "MONEY20")
        mask = paused.fillna(1).lt(0.5)
        mask &= listed.fillna(-1).ge(self.min_listed_days)
        mask &= circ_mv.fillna(0).ge(self.min_circ_mv_yuan)
        mask &= money20.fillna(0).ge(self.min_money_20d_yuan)
        if self.exclude_st:
            is_st = self._feature(df, "IS_ST")
            if self.allow_unknown_st:
                mask &= is_st.fillna(0).lt(0.5)
            else:
                mask &= is_st.notna() & is_st.lt(0.5)
        return df.loc[mask]

    def readonly(self) -> bool:
        return True


class ProcessInfSingleThread(Processor):
    """Replace infinities with each date's finite column mean without worker copies."""

    _BATCH_COLUMNS = 32

    def __init__(self, fields_group=None):
        self.fields_group = fields_group

    def __call__(self, df: pd.DataFrame) -> pd.DataFrame:
        # Alpha158 contains millions of rows. The previous datetime-group
        # callback performed Python work once per date and per column. Process
        # a bounded number of columns at a time instead: pandas performs the
        # grouped reduction in native code and the bounded batch avoids full-
        # frame worker or temporary copies on Windows and macOS.
        for start in range(0, len(df.columns), self._BATCH_COLUMNS):
            columns = df.columns[start : start + self._BATCH_COLUMNS]
            values = df.loc[:, columns]
            infinity = np.isinf(values)
            if not infinity.to_numpy().any():
                continue
            finite_values = values.mask(infinity)
            replacements = finite_values.groupby(level="datetime", sort=False).transform("mean")
            df.loc[:, columns] = values.where(~infinity, replacements)
        df.sort_index(inplace=True)
        return df

    def readonly(self) -> bool:
        return False


class CrossSectionalFactorProcessor(Processor):
    """Winsorize, industry-demean, size-residualize and z-score each date."""

    _SUPPORT = {"INDUSTRY_L1_CODE", "PAUSED", "IS_ST", "LISTED_DAYS", "CIRC_MV", "MONEY20"}

    def __init__(self, minimum_industry_members: int = 5) -> None:
        self.minimum_industry_members = minimum_industry_members

    def __call__(self, df: pd.DataFrame) -> pd.DataFrame:
        if not isinstance(df.columns, pd.MultiIndex) or "feature" not in df.columns.get_level_values(0):
            raise ValueError("multifactor processor requires grouped feature columns")
        features = df["feature"]
        required = {"INDUSTRY_L1_CODE", "LOG_CIRC_MV"}
        missing = required - set(features.columns)
        if missing:
            raise ValueError(f"multifactor support fields are missing: {sorted(missing)}")
        factor_names = [name for name in features.columns if name not in self._SUPPORT]
        processed = features.copy()
        for _, positions in features.groupby(level="datetime", sort=False).indices.items():
            block = features.iloc[positions]
            industry = block["INDUSTRY_L1_CODE"]
            size = pd.to_numeric(block["LOG_CIRC_MV"], errors="coerce")
            for name in factor_names:
                values = pd.to_numeric(block[name], errors="coerce")
                finite = values.replace([np.inf, -np.inf], np.nan)
                if finite.notna().any():
                    lower, upper = finite.quantile([0.01, 0.99])
                    finite = finite.clip(lower, upper)
                if name != "LOG_CIRC_MV":
                    counts = industry.groupby(industry).transform("count")
                    eligible = industry.notna() & counts.ge(self.minimum_industry_members)
                    if eligible.any():
                        means = finite.where(eligible).groupby(industry).transform("mean")
                        finite = finite.where(~eligible, finite - means)
                    regression = pd.DataFrame({"y": finite, "x": size}).dropna()
                    if len(regression) >= 3 and regression["x"].std(ddof=0) > 1e-12:
                        design = np.column_stack(
                            [np.ones(len(regression)), regression["x"].to_numpy(dtype=float)]
                        )
                        coefficients = np.linalg.lstsq(
                            design, regression["y"].to_numpy(dtype=float), rcond=None
                        )[0]
                        residual = regression["y"] - design @ coefficients
                        finite.loc[regression.index] = residual
                center = finite.mean()
                scale = finite.std(ddof=0)
                normalized = (
                    finite - center if not np.isfinite(scale) or scale <= 1e-12 else (finite - center) / scale
                )
                processed.iloc[positions, processed.columns.get_loc(name)] = normalized.to_numpy()
        result = df.copy()
        for name in processed.columns:
            result[("feature", name)] = processed[name]
        result = result.drop(columns=[("feature", "INDUSTRY_L1_CODE")])
        result.sort_index(inplace=True)
        return result

    def readonly(self) -> bool:
        return False


class Phase2FeatureSetProcessor(Processor):
    """Build and isolate a pre-registered Phase 2 feature set by date.

    The handler loads the immutable superset once. This processor performs the
    cross-sectional transformations and then drops every field outside the
    registered ablation, preventing accidental feature leakage between P2 runs.
    """

    _SUPPORT = {"INDUSTRY_L1_CODE", "PAUSED", "IS_ST", "LISTED_DAYS", "CIRC_MV", "MONEY20"}
    _ACCOUNTING = {
        *BENCHMARK_FAMILIES["Profitability"],
        *BENCHMARK_FAMILIES["Growth"],
        *BENCHMARK_FAMILIES["Investment"],
        *BENCHMARK_FAMILIES["Accruals"],
    }

    def __init__(
        self,
        feature_set_id: str,
        selected_technical: tuple[str, ...] | list[str] = (),
        non_applicable_industry_codes: tuple[str, ...] | list[str] = ("801780", "801790"),
        minimum_residual_cross_section: int = 20,
    ) -> None:
        self.feature_set_id = feature_set_id
        self.selected_technical = tuple(str(value) for value in selected_technical)
        self.non_applicable_industry_codes = tuple(str(value) for value in non_applicable_industry_codes)
        self.minimum_residual_cross_section = int(minimum_residual_cross_section)
        self.spec = feature_set(feature_set_id)
        if self.spec.source_pack != "ashare_alpha_phase2_v1":
            raise ValueError(f"{feature_set_id} is not a Phase 2 superset feature set")
        include_selected_technical = bool(getattr(self.spec, "include_selected_technical", False))
        if include_selected_technical and not self.selected_technical:
            raise ValueError("A7 requires a frozen non-empty selected_technical list")
        if not include_selected_technical and self.selected_technical:
            raise ValueError("selected_technical is allowed only for its registered feature set")
        if self.minimum_residual_cross_section < 5:
            raise ValueError("minimum_residual_cross_section must be at least five")

    @staticmethod
    def _rank_z(values: pd.Series) -> pd.Series:
        ranked = (
            pd.to_numeric(values, errors="coerce")
            .replace([np.inf, -np.inf], np.nan)
            .rank(method="average", pct=True)
        )
        scale = ranked.std(ddof=0)
        if not np.isfinite(scale) or scale <= 1e-12:
            return ranked - ranked.mean()
        return (ranked - ranked.mean()) / scale

    def _residual_lowvol(
        self,
        lowvol: pd.Series,
        value: pd.Series,
        profitability: pd.Series,
        size: pd.Series,
        industry: pd.Series,
    ) -> pd.Series:
        controls = pd.DataFrame(
            {"value": value, "profitability": profitability, "size": size},
            index=lowvol.index,
        )
        dummies = pd.get_dummies(industry.astype("string"), prefix="industry", dtype=float)
        design = pd.concat([controls, dummies], axis=1)
        valid = lowvol.notna() & design.notna().all(axis=1)
        result = pd.Series(np.nan, index=lowvol.index, dtype=float)
        if valid.sum() < self.minimum_residual_cross_section:
            return result
        matrix = np.column_stack([np.ones(int(valid.sum())), design.loc[valid].to_numpy(dtype=float)])
        target = lowvol.loc[valid].to_numpy(dtype=float)
        coefficients = np.linalg.lstsq(matrix, target, rcond=None)[0]
        result.loc[valid] = target - matrix @ coefficients
        return self._rank_z(result)

    def __call__(self, df: pd.DataFrame) -> pd.DataFrame:
        if not isinstance(df.columns, pd.MultiIndex) or "feature" not in df.columns.get_level_values(0):
            raise ValueError("Phase 2 feature processor requires grouped feature columns")
        features = df["feature"]
        required = {
            "INDUSTRY_L1_CODE",
            "LOG_TOTAL_MV",
            *{name for names in BENCHMARK_FAMILIES.values() for name in names},
        }
        missing = sorted(required - set(features))
        if missing:
            raise ValueError(f"Phase 2 superset is missing fields: {missing}")
        forbidden = sorted(set(self.selected_technical) & self._SUPPORT)
        unknown_technical = sorted(set(self.selected_technical) - set(features))
        if forbidden or unknown_technical:
            raise ValueError(
                f"invalid selected technical fields: support={forbidden}, unknown={unknown_technical}"
            )

        blocks: list[pd.DataFrame] = []
        for _, positions in features.groupby(level="datetime", sort=False).indices.items():
            block = features.iloc[positions]
            industry = block["INDUSTRY_L1_CODE"]
            industry_codes = industry.astype("string").str.replace(r"\.0$", "", regex=True)
            applicable = ~industry_codes.isin(self.non_applicable_industry_codes)
            normalized = pd.DataFrame(index=block.index)
            for name in sorted(required - {"INDUSTRY_L1_CODE"}):
                values = pd.to_numeric(block[name], errors="coerce")
                if name in self._ACCOUNTING:
                    values = values.where(applicable)
                normalized[name] = self._rank_z(values * ORIENTATIONS.get(name, 1.0))

            composites = {
                "VALUE_COMPOSITE": normalized[list(BENCHMARK_FAMILIES["Value"])].mean(axis=1, skipna=False),
                "PROFITABILITY_COMPOSITE": normalized[list(BENCHMARK_FAMILIES["Profitability"])].mean(
                    axis=1, skipna=False
                ),
                "LOWVOL_COMPOSITE": normalized[list(BENCHMARK_FAMILIES["LowRisk"])].mean(
                    axis=1, skipna=False
                ),
                "SIZE_COMPOSITE": normalized["LOG_TOTAL_MV"],
                "LIQUIDITY_COMPOSITE": normalized[list(BENCHMARK_FAMILIES["Liquidity"])].mean(
                    axis=1, skipna=False
                ),
                "MOMENTUM_COMPOSITE": normalized[list(BENCHMARK_FAMILIES["PriceMomentum"])].mean(
                    axis=1, skipna=False
                ),
            }
            composites["VOLATILITY_COMPOSITE"] = -composites["LOWVOL_COMPOSITE"]
            normalized["FUNDAMENTAL_MOMENTUM"] = normalized[list(BENCHMARK_FAMILIES["Growth"])].mean(
                axis=1, skipna=False
            )
            normalized["LOWVOL_RESIDUAL"] = self._residual_lowvol(
                composites["LOWVOL_COMPOSITE"],
                composites["VALUE_COMPOSITE"],
                composites["PROFITABILITY_COMPOSITE"],
                composites["SIZE_COMPOSITE"],
                industry_codes,
            )
            for name, values in composites.items():
                normalized[name] = values
            for name, (left, right) in INTERACTIONS.items():
                normalized[name] = self._rank_z(normalized[left] * normalized[right])
            for name in self.selected_technical:
                normalized[name] = self._rank_z(block[name])

            explicit_features = getattr(self.spec, "features", ())
            selected: list[str] = list(explicit_features)
            if not explicit_features:
                for family in self.spec.families:
                    if family in BENCHMARK_FAMILIES:
                        selected.extend(BENCHMARK_FAMILIES[family])
                    elif family == "FundamentalMomentum":
                        selected.append("FUNDAMENTAL_MOMENTUM")
                    elif family == "ResidualLowRisk":
                        selected.append("LOWVOL_RESIDUAL")
                    else:
                        raise ValueError(f"unsupported Phase 2 family in {self.feature_set_id}: {family}")
                selected.extend(self.selected_technical)
                if self.spec.include_interactions:
                    selected.extend(INTERACTIONS)
            selected = list(dict.fromkeys(selected))
            blocks.append(normalized[selected])

        processed = pd.concat(blocks).sort_index()
        processed.columns = pd.MultiIndex.from_product([["feature"], processed.columns])
        non_feature = df.loc[:, df.columns.get_level_values(0) != "feature"]
        return pd.concat([processed, non_feature], axis=1).sort_index()

    def readonly(self) -> bool:
        return False
