from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import qlib_platform.releases.publisher as publisher_module
from qlib_platform.datasets.qlib_staging_contract import (
    QlibStagingContractError,
    validate_qlib_staging_files,
)
from qlib_platform.releases.publisher import ComponentSource, LocalReleasePublisher


def test_numeric_chunk_name_is_valid_when_schema_matches(tmp_path: Path) -> None:
    chunk = tmp_path / "00000.parquet"
    pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-09-01", "2026-09-02"]),
            "symbol": ["SH600000", "SH600000"],
            "close": [10.0, 10.1],
        }
    ).to_parquet(chunk, index=False)

    validate_qlib_staging_files([chunk])


def test_schema_gate_rejects_chunk_without_date_and_symbol(tmp_path: Path) -> None:
    chunk = tmp_path / "00000.parquet"
    pd.DataFrame({"trade_date": ["20260901"], "ts_code": ["600000.SH"]}).to_parquet(chunk, index=False)

    with pytest.raises(QlibStagingContractError, match="date and symbol columns: 00000.parquet"):
        validate_qlib_staging_files([chunk])


def test_local_publisher_rejects_invalid_qlib_staging_before_freezing(tmp_path: Path, monkeypatch) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    pd.DataFrame({"trade_date": ["20260901"], "ts_code": ["600000.SH"]}).to_parquet(
        staging / "00000.parquet", index=False
    )
    profile = "test_qlib_staging_contract"
    monkeypatch.setitem(publisher_module.DATA_RELEASE_PROFILES, profile, frozenset({"qlib_staging"}))

    with pytest.raises(QlibStagingContractError, match="date and symbol columns: 00000.parquet"):
        LocalReleasePublisher(tmp_path / "releases").publish(
            profile=profile,
            components=[
                ComponentSource(
                    "qlib_staging",
                    staging,
                    schema_version="qlib-staging-v2",
                )
            ],
            coverage={"start": "2026-09-01", "end": "2026-09-01"},
        )

    assert not list((tmp_path / "releases").glob("ds_*"))
