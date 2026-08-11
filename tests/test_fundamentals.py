import sys
from pathlib import Path
from types import ModuleType

import pandas as pd
import pytest

from tushare_qlib.custom_handler import (
    PIT_FEATURE_EXPRESSIONS,
    PIT_FEATURE_NAMES,
    TushareAlpha158Daily,
    TushareAlpha158Fundamental,
)
from tushare_qlib.fundamentals import PIT_FIELDS, build_pit_fundamentals
from tushare_qlib.normalize import _merge_pit_fundamentals
from tushare_qlib.qlib_export import smoke_test_dataset
from tushare_qlib.settings import Paths, Settings


def test_pit_fundamentals_do_not_leak_before_announcement():
    reports = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "end_date": ["2025-12-31"],
            "ann_date": ["2026-03-30"],
            "roe_waa_pit": [0.1],
            "roa_pit": [0.05],
            "netprofit_margin_pit": [0.1],
            "netprofit_yoy_pit": [0.2],
            "or_yoy_pit": [0.1],
            "debt_to_assets_pit": [0.4],
            "ocf_to_or_pit": [0.2],
        }
    )
    calendar = pd.DataFrame({"cal_date": ["2026-03-27", "2026-03-30", "2026-03-31"], "is_open": [1, 1, 1]})
    result = build_pit_fundamentals(reports, calendar)
    assert result["trade_date"].dt.strftime("%Y-%m-%d").tolist() == ["2026-03-30", "2026-03-31"]


def _report(ann_date: str, value: float) -> dict[str, object]:
    return {
        "ts_code": "000001.SZ",
        "end_date": "2025-12-31",
        "ann_date": ann_date,
        **{field: value for field in PIT_FIELDS},
    }


def test_weekend_announcement_maps_to_next_open_day_and_restatement_is_asof():
    reports = pd.DataFrame([_report("2026-03-28", 0.1), _report("2026-04-04", 0.2)])
    calendar = pd.DataFrame(
        {
            "cal_date": ["2026-03-27", "2026-03-30", "2026-04-03", "2026-04-06"],
            "is_open": [1, 1, 1, 1],
        }
    )

    result = build_pit_fundamentals(reports, calendar)

    assert result["trade_date"].dt.strftime("%Y-%m-%d").tolist() == [
        "2026-03-30",
        "2026-04-03",
        "2026-04-06",
    ]
    assert result["roe_waa_pit"].tolist() == [0.1, 0.1, 0.2]


def test_pit_fundamentals_merge_into_daily_curated_partition(tmp_path: Path):
    paths = Paths.from_root(tmp_path / "data")
    paths.curated.mkdir(parents=True)
    pd.DataFrame(
        [{"ts_code": "000001.SZ", "trade_date": "2026-03-30", **{field: 0.1 for field in PIT_FIELDS}}]
    ).to_parquet(paths.curated / "fundamentals_pit.parquet", index=False)
    settings = Settings(tmp_path / "pipeline.yaml", {}, paths, None, None, tmp_path / "qlib")
    daily = pd.DataFrame({"ts_code": ["000001.SZ", "600000.SH"], "trade_date": ["20260330"] * 2})

    result = _merge_pit_fundamentals(daily, settings, "20260330")

    assert result.loc[0, "roe_waa_pit"] == pytest.approx(0.1)
    assert pd.isna(result.loc[1, "roe_waa_pit"])
    assert set(PIT_FIELDS).issubset(result.columns)


def test_fundamental_handler_asserts_all_pit_model_features(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(TushareAlpha158Daily, "get_feature_config", lambda self: (["$close"], ["CLOSE"]))
    handler = object.__new__(TushareAlpha158Fundamental)

    fields, names = handler.get_feature_config()

    assert fields[-len(PIT_FIELDS) :] == list(PIT_FEATURE_EXPRESSIONS)
    assert names[-len(PIT_FIELDS) :] == list(PIT_FEATURE_NAMES)


def test_qlib_smoke_queries_all_pit_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    class FakeData:
        @staticmethod
        def calendar(freq: str):
            return [pd.Timestamp("2026-03-30")]

        @staticmethod
        def list_instruments(config: object, as_list: bool):
            return ["SZ000001"]

        @staticmethod
        def features(instruments: object, fields: list[str], **kwargs: object):
            captured["fields"] = fields
            return pd.DataFrame({field: [0.1] for field in fields})

    qlib = ModuleType("qlib")
    qlib.init = lambda **kwargs: None  # type: ignore[attr-defined]
    constant = ModuleType("qlib.constant")
    constant.REG_CN = "cn"  # type: ignore[attr-defined]
    data = ModuleType("qlib.data")
    data.D = FakeData()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "qlib", qlib)
    monkeypatch.setitem(sys.modules, "qlib.constant", constant)
    monkeypatch.setitem(sys.modules, "qlib.data", data)

    smoke = smoke_test_dataset(tmp_path)

    assert set(PIT_FEATURE_EXPRESSIONS).issubset(captured["fields"])
    assert set(PIT_FEATURE_EXPRESSIONS).issubset(smoke["queried_fields"])
