from __future__ import annotations

import numpy as np
import pandas as pd
from qlib.data.dataset.processor import Processor


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
