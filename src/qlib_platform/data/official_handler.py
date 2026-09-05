from __future__ import annotations

from typing import Any

from qlib.contrib.data.handler import Alpha158


class QlibOfficialAlpha158(Alpha158):
    """Alpha158 control that preserves Microsoft's benchmark preprocessing.

    qlib-platform still owns the pinned local DatasetVersion and research
    lineage.  This handler only restores the upstream Alpha158 processor
    contract so the control is not affected by the platform's production
    RobustZScore/Fillna/CSRank preprocessing recipe.
    """

    @staticmethod
    def processor_config() -> dict[str, list[dict[str, Any]]]:
        return {
            "shared_processors": [],
            "infer_processors": [],
            "learn_processors": [
                {"class": "DropnaLabel"},
                {"class": "CSZScoreNorm", "kwargs": {"fields_group": "label"}},
            ],
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.update(self.processor_config())
        super().__init__(*args, **kwargs)
