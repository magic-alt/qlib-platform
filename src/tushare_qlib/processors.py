from __future__ import annotations

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
