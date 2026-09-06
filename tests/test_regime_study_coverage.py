from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from qlib_platform.data.store import sha256_file
from qlib_platform.research.studies import regime


class _Release:
    def __init__(self, files: dict[str, list[Path]], components: set[str] | None = None):
        self._files = files
        self.components = components or set(files)

    def files(self, name: str) -> list[Path]:
        return self._files[name]


def test_json_and_base_manifest_validation(tmp_path) -> None:
    path = tmp_path / "manifest.json"
    with pytest.raises(FileNotFoundError):
        regime._load_json(path, "test")
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        regime._load_json(path, "test")

    artifact = tmp_path / "feature.parquet"
    artifact.write_text("evidence", encoding="utf-8")
    payload = {
        "status": {"featureDiagnostics": "PASS"},
        "selectionUsesFinalHoldout": False,
        "publishingAuthorized": False,
        "artifacts": [
            {"name": artifact.name, "path": artifact.name, "sha256": sha256_file(artifact)}
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert regime._validate_base_study(path)["status"]["featureDiagnostics"] == "PASS"
    payload["status"]["featureDiagnostics"] = "FAIL"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="not PASS"):
        regime._validate_base_study(path)
    payload["status"]["featureDiagnostics"] = "PASS"
    payload["selectionUsesFinalHoldout"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="holdout"):
        regime._validate_base_study(path)
    payload["selectionUsesFinalHoldout"] = False
    payload["publishingAuthorized"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="publishing"):
        regime._validate_base_study(path)


def test_artifact_path_requires_one_local_artifact(tmp_path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}")
    artifact = tmp_path / "a.parquet"
    artifact.write_text("x")
    manifest = {"artifacts": [{"name": "a", "path": artifact.name}]}
    assert regime._artifact_path(manifest_path, manifest, "a") == artifact.resolve()
    with pytest.raises(ValueError, match="exactly one"):
        regime._artifact_path(manifest_path, {"artifacts": []}, "a")
    with pytest.raises(ValueError, match="escapes"):
        regime._artifact_path(
            manifest_path,
            {"artifacts": [{"name": "a", "path": "../escape.parquet"}]},
            "a",
        )


def test_instrument_code_conversions() -> None:
    assert regime._to_tushare_code("sh600000") == "600000.SH"
    assert regime._to_tushare_code("SZ000001") == "000001.SZ"
    assert regime._to_qlib_instrument("600000.SH") == "SH600000"
    assert regime._to_qlib_instrument("000001.sz") == "SZ000001"
    with pytest.raises(ValueError, match="invalid Qlib"):
        regime._to_tushare_code("600000.SH")
    with pytest.raises(ValueError, match="invalid Tushare"):
        regime._to_qlib_instrument("SH600000")


def test_load_benchmark_close_filters_csi300_and_validates(tmp_path) -> None:
    file = tmp_path / "benchmark.parquet"
    pd.DataFrame(
        {
            "ts_code": ["000300.SH", "000905.SH", "000300.SH"],
            "trade_date": ["20260901", "20260901", "20260902"],
            "close": [4000.0, 6000.0, 4010.0],
        }
    ).to_parquet(file, index=False)
    result = regime._load_benchmark_close(_Release({"benchmark": [file]}))
    assert result.tolist() == [4000.0, 4010.0]
    pd.DataFrame({"trade_date": ["20260901"]}).to_parquet(file, index=False)
    with pytest.raises(ValueError, match="requires trade_date and close"):
        regime._load_benchmark_close(_Release({"benchmark": [file]}))
    pd.DataFrame(
        {"trade_date": ["20260901", "20260901"], "close": [1.0, 2.0]}
    ).to_parquet(file, index=False)
    with pytest.raises(ValueError, match="duplicated"):
        regime._load_benchmark_close(_Release({"benchmark": [file]}))


def test_load_pit_industries_maps_intervals_and_detects_bad_schema(tmp_path) -> None:
    file = tmp_path / "industry.parquet"
    pd.DataFrame(
        {
            "instrument": ["SZ000001", "SH600000"],
            "effective_from": ["2026-01-01", "2026-01-01"],
            "effective_to": ["2026-12-31", "2026-12-31"],
            "industry_code": ["801780", "801010"],
            "taxonomy": ["SW2021", "SW2021"],
            "level_no": [1, 1],
        }
    ).to_parquet(file, index=False)
    index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2026-09-01"), "SZ000001"), (pd.Timestamp("2026-09-01"), "SH600000")],
        names=["datetime", "instrument"],
    )
    release = _Release({"industry_classification_pit": [file]})
    result = regime._load_pit_industries(release, index)
    assert result is not None
    assert result.tolist() == ["801780", "801010"]
    assert regime._load_pit_industries(_Release({}, components=set()), index) is None

    pd.DataFrame({"instrument": ["SZ000001"]}).to_parquet(file, index=False)
    with pytest.raises(ValueError, match="incomplete schema"):
        regime._load_pit_industries(release, index)
    pd.DataFrame(
        {
            "instrument": ["SZ000001"],
            "effective_from": ["2026-01-01"],
            "effective_to": ["2026-12-31"],
            "industry_code": ["x"],
            "taxonomy": ["OTHER"],
            "level_no": [2],
        }
    ).to_parquet(file, index=False)
    with pytest.raises(ValueError, match="SW2021"):
        regime._load_pit_industries(release, index)


def test_load_pit_industries_rejects_overlapping_intervals(tmp_path) -> None:
    file = tmp_path / "industry.parquet"
    pd.DataFrame(
        {
            "instrument": ["SZ000001", "SZ000001"],
            "effective_from": ["2026-01-01", "2026-06-01"],
            "effective_to": ["2026-12-31", "2026-12-31"],
            "industry_code": ["a", "b"],
            "taxonomy": ["SW2021", "SW2021"],
            "level_no": [1, 1],
        }
    ).to_parquet(file, index=False)
    index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2026-09-01"), "SZ000001")], names=["datetime", "instrument"]
    )
    with pytest.raises(ValueError, match="overlapping"):
        regime._load_pit_industries(_Release({"industry_classification_pit": [file]}), index)


def test_history_start_and_availability() -> None:
    benchmark = pd.Series(
        range(100), index=pd.date_range("2026-01-01", periods=100, freq="B"), dtype=float
    )
    evaluation = pd.date_range(benchmark.index[-10], periods=5, freq="B")
    spec = SimpleNamespace(
        dimensions={
            "size_style": {"window": 20, "bucketLagSessions": 1},
            "industry_breadth": {"window": 10},
        },
        minimum_sessions=2,
    )
    assert regime._history_start(benchmark, evaluation, spec) < evaluation.min()
    with pytest.raises(ValueError, match="insufficient"):
        regime._history_start(benchmark.iloc[:5], evaluation, spec)

    labels = pd.DataFrame(
        [
            {"dimension": dimension, "status": "AVAILABLE", "state": "A", "date": date}
            for dimension in regime.REQUIRED_DIMENSIONS
            for date in pd.to_datetime(["2026-09-01", "2026-09-02"])
        ]
    )
    availability = regime._availability(labels, spec)
    assert all(item["status"] == "AVAILABLE" for item in availability.values())
    assert all(item["inferenceEligibleStates"] == ["A"] for item in availability.values())


def test_artifact_entry_includes_optional_rows(tmp_path) -> None:
    path = tmp_path / "artifact.csv"
    path.write_text("x\n1\n", encoding="utf-8")
    basic = regime._artifact_entry(path)
    assert basic["name"] == "artifact.csv"
    assert "rows" not in basic
    with_rows = regime._artifact_entry(path, rows=1)
    assert with_rows["rows"] == 1
