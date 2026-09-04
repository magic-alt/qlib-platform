from pathlib import Path

import pandas as pd
import pytest

from qlib_platform.fundamentals import build_pit_from_extended
from qlib_platform.settings import Paths, Settings


def _settings_with_reports(tmp_path: Path, reports: pd.DataFrame) -> Settings:
    paths = Paths.from_root(tmp_path / "data")
    source = paths.raw / "extended" / "fina_indicator_vip" / "trade_date=20260331"
    source.mkdir(parents=True)
    reports.to_parquet(source / "data.parquet", index=False)
    paths.metadata.mkdir(parents=True)
    pd.DataFrame({"cal_date": ["2026-03-30", "2026-03-31"], "is_open": [1, 1]}).to_parquet(
        paths.metadata / "trade_calendar.parquet", index=False
    )
    return Settings(tmp_path / "pipeline.yaml", {}, paths, None, None, tmp_path / "qlib")


def _current_schema_report(*, ts_code: str = "000001.SZ", ann_date: str | None = "2026-03-30"):
    return {
        "ts_code": ts_code,
        "end_date": "2025-12-31",
        "ann_date": ann_date,
        "roe_waa": 0.1,
        "roa": 0.05,
        "netprofit_margin": 0.1,
        "netprofit_yoy": 0.2,
        "or_yoy": 0.1,
        "debt_to_assets": 0.4,
        "q_ocf_to_sales": 0.2,
    }


def test_build_pit_from_extended_maps_current_cashflow_schema_and_ignores_invalid_rows(
    tmp_path: Path,
):
    settings = _settings_with_reports(
        tmp_path,
        pd.DataFrame(
            [
                _current_schema_report(),
                _current_schema_report(ts_code="000002.SZ", ann_date=None),
            ]
        ),
    )

    target = build_pit_from_extended(settings)

    result = pd.read_parquet(target)
    assert result["ts_code"].tolist() == ["000001.SZ"]
    assert result["ocf_to_or_pit"].tolist() == [pytest.approx(0.2)]


def test_build_pit_from_extended_fails_closed_when_all_rows_have_invalid_dates(tmp_path: Path):
    settings = _settings_with_reports(
        tmp_path,
        pd.DataFrame([_current_schema_report(ann_date=None)]),
    )

    with pytest.raises(ValueError, match="no reports with valid identifiers and dates"):
        build_pit_from_extended(settings)


def test_build_pit_from_extended_requires_a_supported_cashflow_column(tmp_path: Path):
    reports = pd.DataFrame([_current_schema_report()]).drop(columns="q_ocf_to_sales")
    settings = _settings_with_reports(tmp_path, reports)

    with pytest.raises(ValueError, match="missing ocf_to_or and q_ocf_to_sales"):
        build_pit_from_extended(settings)


def test_build_pit_from_extended_coalesces_mixed_cashflow_schemas(tmp_path: Path):
    reports = pd.DataFrame(
        [
            _current_schema_report(ts_code="000001.SZ"),
            _current_schema_report(ts_code="000002.SZ"),
        ]
    )
    reports["ocf_to_or"] = [0.3, None]
    reports.loc[reports["ts_code"] == "000001.SZ", "q_ocf_to_sales"] = 9.9
    settings = _settings_with_reports(tmp_path, reports)

    target = build_pit_from_extended(settings)

    result = pd.read_parquet(target).set_index("ts_code")
    assert result.loc["000001.SZ", "ocf_to_or_pit"] == pytest.approx(0.3)
    assert result.loc["000002.SZ", "ocf_to_or_pit"] == pytest.approx(0.2)
