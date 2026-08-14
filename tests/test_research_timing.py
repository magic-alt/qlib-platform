from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from tushare_qlib.research_timing import shared_research_calendar
from tushare_qlib.settings import Paths, Settings


def test_platform_research_calendar_uses_release_instead_of_legacy_raw(tmp_path, monkeypatch):
    paths = Paths.from_root(tmp_path / "data")
    qlib_dir = tmp_path / "qlib"
    (qlib_dir / "calendars").mkdir(parents=True)
    (qlib_dir / "calendars" / "day.txt").write_text("2024-01-02\n2024-01-03\n", encoding="utf-8")
    release_calendar = tmp_path / "release_calendar.parquet"
    pd.DataFrame(
        {
            "cal_date": ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"],
            "is_open": [0, 1, 1, 1],
        }
    ).to_parquet(release_calendar, index=False)
    settings = Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={"data_source": {"kind": "platform_release"}},
        paths=paths,
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=qlib_dir,
    )
    release = SimpleNamespace(
        coverage={"start": "2024-01-02", "end": "2024-01-03"},
        files=lambda role: [release_calendar] if role == "trading_calendar" else [],
    )
    monkeypatch.setattr("tushare_qlib.dataset_resolver.pin_dataset", lambda value: (value, None))
    monkeypatch.setattr("tushare_qlib.platform_release.load_platform_release", lambda value: release)

    actual = shared_research_calendar(settings)

    assert actual.tolist() == [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03")]
