from __future__ import annotations

import pandas as pd

from tushare_qlib.settings import Paths, Settings
from tushare_qlib.universe import build_membership_intervals, install_qlib_universe, write_membership


def _settings(tmp_path):
    return Settings(
        config_path=tmp_path / "configs" / "pipeline.yaml",
        data={"universe": {"instruments": "csi300", "index_code": "399300.SZ"}},
        paths=Paths.from_root(tmp_path / "data"),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib",
    )


def test_membership_uses_next_open_day_and_never_backfills_future_snapshot(tmp_path):
    calendar = pd.DatetimeIndex(pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]))
    snapshots = pd.DataFrame(
        {
            "trade_date": ["20240102", "20240102", "20240104"],
            "con_code": ["600000.SH", "000001.SZ", "000001.SZ"],
            "weight": [1.0, 2.0, 3.0],
        }
    )

    result = build_membership_intervals(snapshots, calendar, universe_code="399300.SZ", effective_lag_days=1)

    first = result[result["snapshot_date"] == pd.Timestamp("2024-01-02")]
    second = result[result["snapshot_date"] == pd.Timestamp("2024-01-04")]
    assert set(first["instrument"]) == {"SH600000", "SZ000001"}
    assert first["effective_from"].unique().tolist() == [pd.Timestamp("2024-01-03")]
    assert first["effective_to"].unique().tolist() == [pd.Timestamp("2024-01-04")]
    assert second["effective_from"].unique().tolist() == [pd.Timestamp("2024-01-05")]
    assert result["effective_from"].min() > calendar.min()


def test_membership_is_installed_as_dynamic_qlib_market(tmp_path):
    settings = _settings(tmp_path)
    intervals = pd.DataFrame(
        {
            "universe_code": ["399300.SZ"],
            "instrument": ["SH600000"],
            "snapshot_date": pd.to_datetime(["2024-01-02"]),
            "effective_from": pd.to_datetime(["2024-01-03"]),
            "effective_to": pd.to_datetime(["2024-01-31"]),
            "weight": [1.0],
        }
    )
    write_membership(settings, intervals)

    target = install_qlib_universe(settings, settings.qlib_data_uri)

    assert target is not None
    assert target.read_text(encoding="utf-8") == "SH600000\t2024-01-03\t2024-01-31\n"
