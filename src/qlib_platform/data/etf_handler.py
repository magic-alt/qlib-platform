from __future__ import annotations

from typing import Any

from qlib.contrib.data.handler import Alpha158

ETF_CORE_FEATURE_EXPRESSIONS = (
    "$close/Ref($close, 1)-1",
    "$close/Ref($close, 5)-1",
    "$close/Ref($close, 20)-1",
    "$close/Ref($close, 60)-1",
    "$close/Mean($close, 5)-1",
    "$close/Mean($close, 20)-1",
    "Mean($close, 5)/Mean($close, 20)-1",
    "Std($close/Ref($close, 1)-1, 5)",
    "Std($close/Ref($close, 1)-1, 20)",
    "($high-$low)/($close+1e-12)",
    "Log(Mean($money, 20)+1)",
    "$money/(Mean($money, 20)+1)",
    "$volume/(Mean($volume, 20)+1)",
)
ETF_CORE_FEATURE_NAMES = (
    "RET_1",
    "RET_5",
    "RET_20",
    "RET_60",
    "CLOSE_TO_MA_5",
    "CLOSE_TO_MA_20",
    "MA5_TO_MA20",
    "VOL_5",
    "VOL_20",
    "RANGE_TO_CLOSE",
    "LOG_MONEY_20",
    "MONEY_RATIO_20",
    "VOLUME_RATIO_20",
)


def etf_shared_processors(processors: object) -> object:
    """Remove only the stock-specific universe processor from an ETF handler pipeline."""

    if not isinstance(processors, list):
        return processors
    return [
        processor
        for processor in processors
        if not (
            isinstance(processor, dict)
            and processor.get("class") == "AshareUniverseFilter"
        )
    ]


class AshareEtfCore(Alpha158):
    """Technical/liquidity ETF feature set with no stock fundamental dependencies."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if "shared_processors" in kwargs:
            kwargs["shared_processors"] = etf_shared_processors(kwargs["shared_processors"])
        super().__init__(*args, **kwargs)

    def get_feature_config(self):
        return list(ETF_CORE_FEATURE_EXPRESSIONS), list(ETF_CORE_FEATURE_NAMES)
