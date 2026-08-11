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
