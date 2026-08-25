from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from tushare_qlib.releases import import_qlib_dataset
from tushare_qlib.settings import Settings


def _field(root: Path, instrument: str, values: np.ndarray) -> None:
    target = root / "features" / instrument.lower() / "close.day.bin"
    target.parent.mkdir(parents=True, exist_ok=True)
    np.concatenate([np.asarray([0.0]), values]).astype("<f4").tofile(target)


def _provider(root: Path) -> pd.DatetimeIndex:
    dates = pd.bdate_range("2026-01-05", periods=36)
    (root / "calendars").mkdir(parents=True)
    (root / "instruments").mkdir()
    (root / "calendars" / "day.txt").write_text(
        "\n".join(dates.strftime("%Y-%m-%d")) + "\n", encoding="utf-8"
    )
    instruments = ("sh600000", "sz000001", "sz000002")
    (root / "instruments" / "all.txt").write_text(
        "".join(f"{name}\t{dates[0].date()}\t{dates[-1].date()}\n" for name in instruments),
        encoding="utf-8",
    )
    time = np.arange(len(dates), dtype=float)
    _field(root, instruments[0], 10 + 0.08 * time + 0.02 * np.sin(time))
    _field(root, instruments[1], 11 + 0.04 * time + 0.03 * np.cos(time))
    _field(root, instruments[2], 9 + 0.06 * time - 0.02 * np.sin(time))
    return dates


def _settings(tmp_path: Path) -> Settings:
    config = tmp_path / "pipeline.yaml"
    config.write_text(
        "\n".join(
            [
                "mode: standalone",
                f"project_root: {tmp_path / 'data'}",
                "data_source: {kind: auto}",
                "storage: {registry_path: ''}",
                "release_store: {kind: file, root: ''}",
                "qlib:",
                "  dataset_dir: ''",
                "  versions_root: ''",
                "  dataset_name: isolation_cn",
                "  dataset_ref: research-current",
            ]
        ),
        encoding="utf-8",
    )
    return Settings.load(config)


def test_standalone_import_feature_train_and_backtest_without_platform(tmp_path: Path, monkeypatch):
    for name in ("PLATFORM_URL", "QUANT_DATA_ROOT", "DATASET_RELEASE_ID", "TUSHARE_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("tushare_qlib.settings.load_dotenv", lambda: None)
    settings = _settings(tmp_path)
    dates = _provider(tmp_path / "legacy_qlib")
    release, dataset = import_qlib_dataset(settings, tmp_path / "legacy_qlib")

    import qlib
    from qlib.constant import REG_CN
    from qlib.data import D

    qlib.init(
        provider_uri=str(dataset.data_path),
        region=REG_CN,
        expression_cache=None,
        dataset_cache=None,
        kernels=1,
    )
    values = D.features(
        ["SH600000", "SZ000001", "SZ000002"],
        ["$close"],
        start_time=str(dates[0].date()),
        end_time=str(dates[-1].date()),
    )["$close"].unstack("instrument")
    features = values.pct_change()
    labels = values.pct_change().shift(-1)
    stacked = pd.concat({"feature": features.stack(), "label": labels.stack()}, axis=1).dropna()
    train = stacked.loc[stacked.index.get_level_values("datetime") <= dates[23]]
    test = stacked.loc[stacked.index.get_level_values("datetime") > dates[23]]
    design = np.column_stack([np.ones(len(train)), train["feature"].to_numpy()])
    coefficients = np.linalg.lstsq(design, train["label"].to_numpy(), rcond=None)[0]
    test = test.assign(score=coefficients[0] + coefficients[1] * test["feature"])
    selected = test.loc[test.groupby(level="datetime")["score"].idxmax()]

    assert release.data_release_id == dataset.data_release_id
    assert len(values) == len(dates)
    assert len(selected) > 0
    assert np.isfinite(selected["label"].mean())
