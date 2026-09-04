from __future__ import annotations

from pathlib import Path

import pandas as pd

from qlib_platform.data.ingestion import Extractor
from qlib_platform.datasets.qlib_export import _portable_dataset_dir
from qlib_platform.settings import Paths, Settings
from qlib_platform.research.train_select import _sqlite_tracking_uri


def _settings(tmp_path: Path) -> Settings:
    paths = Paths.from_root(tmp_path / "data")
    paths.mkdirs()
    return Settings(
        config_path=tmp_path / "configs" / "pipeline.yaml",
        data={"qlib": {"dataset_dir": "unused", "dataset_version": "cn_tushare_v1"}, "tushare": {}},
        paths=paths,
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=paths.root / "qlib" / "cn_tushare_v1",
    )


def test_sqlite_uri_and_dataset_manifest_path_are_portable(tmp_path):
    settings = _settings(tmp_path)
    uri = _sqlite_tracking_uri(settings.paths.state / "mlflow.db")

    assert uri.startswith("sqlite:///")
    assert "\\" not in uri
    assert _portable_dataset_dir(settings) == "qlib/cn_tushare_v1"


def test_tushare_benchmark_sync_writes_canonical_series(tmp_path):
    settings = _settings(tmp_path)

    class Client:
        def call(self, endpoint, **kwargs):
            assert endpoint == "index_daily"
            assert kwargs["ts_code"] == "000300.SH"
            return pd.DataFrame(
                {"trade_date": ["20260807", "20260806"], "close": [4100.0, 4050.0], "open": [4080.0, 4040.0]}
            )

    extractor = object.__new__(Extractor)
    extractor.settings = settings
    extractor.source_is_mysql = False
    extractor.client = Client()
    frame = extractor.sync_benchmark("SH000300", "20260806", "20260807")

    assert frame["trade_date"].dt.strftime("%Y%m%d").tolist() == ["20260806", "20260807"]
    assert (settings.paths.metadata / "benchmarks" / "SH000300.parquet").exists()
