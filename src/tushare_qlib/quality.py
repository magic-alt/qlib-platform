from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class QualityResult:
    name: str
    passed: bool
    detail: str


def validate_curated(df: pd.DataFrame) -> list[QualityResult]:
    results: list[QualityResult] = []
    duplicated = int(df.duplicated(["symbol", "date"]).sum())
    results.append(QualityResult("unique_symbol_date", duplicated == 0, f"duplicates={duplicated}"))

    traded = df["close"].notna()
    bad_price = int((df.loc[traded, ["open", "high", "low", "close"]].le(0).any(axis=1)).sum())
    results.append(QualityResult("positive_prices", bad_price == 0, f"bad_rows={bad_price}"))

    bad_ohlc = int(((df.loc[traded, "high"] < df.loc[traded, ["open", "close", "low"]].max(axis=1)) |
                    (df.loc[traded, "low"] > df.loc[traded, ["open", "close", "high"]].min(axis=1))).sum())
    results.append(QualityResult("ohlc_relation", bad_ohlc == 0, f"bad_rows={bad_ohlc}"))

    bad_factor = int((df.loc[traded, "adj_factor"].fillna(0) <= 0).sum())
    results.append(QualityResult("positive_adj_factor", bad_factor == 0, f"bad_rows={bad_factor}"))
    return results


def assert_quality(results: list[QualityResult]) -> None:
    failures = [r for r in results if not r.passed]
    if failures:
        raise AssertionError("; ".join(f"{r.name}: {r.detail}" for r in failures))
