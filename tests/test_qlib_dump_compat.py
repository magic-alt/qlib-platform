from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from qlib_platform.datasets.qlib_dump_compat import dump_qlib_bin


def _write(path: Path, dates: list[str], close: list[float]) -> None:
    pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "symbol": ["SH600000"] * len(dates),
            "close": close,
            "volume": [100.0 + index for index in range(len(dates))],
        }
    ).to_parquet(path, index=False)


def _bin(provider: Path, field: str) -> np.ndarray:
    return np.fromfile(provider / "features" / "sh600000" / f"{field}.day.bin", dtype="<f4")


def test_packaged_dump_all_update_and_fix(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    provider = tmp_path / "provider"
    source = staging / "SH600000.parquet"

    _write(source, ["2026-09-01", "2026-09-02"], [10.0, 11.0])
    dump_qlib_bin(
        "dump_all",
        data_path=staging,
        qlib_dir=provider,
        include_fields=("close", "volume"),
    )

    assert (provider / "calendars" / "day.txt").read_text(encoding="utf-8").splitlines() == [
        "2026-09-01",
        "2026-09-02",
    ]
    assert (provider / "instruments" / "all.txt").read_text(encoding="utf-8").strip() == (
        "SH600000\t2026-09-01\t2026-09-02"
    )
    np.testing.assert_allclose(_bin(provider, "close"), np.asarray([0.0, 10.0, 11.0], dtype="<f4"))

    _write(source, ["2026-09-03"], [12.0])
    dump_qlib_bin(
        "dump_update",
        data_path=staging,
        qlib_dir=provider,
        include_fields=("close", "volume"),
    )
    assert (provider / "calendars" / "day.txt").read_text(encoding="utf-8").splitlines() == [
        "2026-09-01",
        "2026-09-02",
        "2026-09-03",
    ]
    np.testing.assert_allclose(
        _bin(provider, "close"), np.asarray([0.0, 10.0, 11.0, 12.0], dtype="<f4")
    )

    _write(source, ["2026-09-01", "2026-09-02", "2026-09-03"], [10.0, 99.0, 12.0])
    dump_qlib_bin(
        "dump_fix",
        data_path=staging,
        qlib_dir=provider,
        include_fields=("close", "volume"),
    )
    np.testing.assert_allclose(
        _bin(provider, "close"), np.asarray([0.0, 10.0, 99.0, 12.0], dtype="<f4")
    )
