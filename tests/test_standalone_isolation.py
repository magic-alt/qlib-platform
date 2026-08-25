from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from tushare_qlib.dataset_registry import DatasetRegistry
from tushare_qlib.settings import Settings


FIELDS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "money",
    "vwap",
    "factor",
    "is_limit_up",
    "is_limit_down",
)


def _field(root: Path, instrument: str, name: str, values: np.ndarray) -> None:
    target = root / "features" / instrument.lower() / f"{name}.day.bin"
    target.parent.mkdir(parents=True, exist_ok=True)
    np.concatenate([np.asarray([0.0]), values]).astype("<f4").tofile(target)


def _provider(root: Path) -> pd.DatetimeIndex:
    dates = pd.bdate_range("2025-07-01", periods=150)
    (root / "calendars").mkdir(parents=True)
    (root / "instruments").mkdir()
    (root / "calendars" / "day.txt").write_text(
        "\n".join(dates.strftime("%Y-%m-%d")) + "\n", encoding="utf-8"
    )
    instruments = tuple(f"sh60000{index}" for index in range(6))
    (root / "instruments" / "all.txt").write_text(
        "".join(f"{name}\t{dates[0].date()}\t{dates[-1].date()}\n" for name in instruments),
        encoding="utf-8",
    )
    time = np.arange(len(dates), dtype=float)
    for index, instrument in enumerate(instruments):
        close = (
            8.0
            + index
            + (0.018 + index * 0.002) * time
            + 0.08 * np.sin(time / (4.0 + index))
            + 0.03 * np.cos(time / 7.0 + index)
        )
        open_price = close * (1.0 + 0.001 * np.sin(time + index))
        volume = 1_000_000.0 + index * 50_000.0 + 10_000.0 * np.cos(time / 3.0)
        values = {
            "open": open_price,
            "high": np.maximum(open_price, close) * 1.01,
            "low": np.minimum(open_price, close) * 0.99,
            "close": close,
            "volume": volume,
            "money": close * volume,
            "vwap": close,
            "factor": np.ones(len(dates)),
            "is_limit_up": np.zeros(len(dates)),
            "is_limit_down": np.zeros(len(dates)),
        }
        for name in FIELDS:
            _field(root, instrument, name, values[name])
    return dates


def _settings(tmp_path: Path) -> Settings:
    config = tmp_path / "pipeline.yaml"
    config.write_text(
        "\n".join(
            [
                "mode: standalone",
                f"project_root: {tmp_path / 'data'}",
                "start_date: '20250701'",
                "end_date: '20260126'",
                "data_source: {kind: auto}",
                "storage: {registry_path: ''}",
                "release_store: {kind: file, root: ''}",
                "experiment:",
                "  alpha: {pack: alpha158_market_v1}",
                "  label: {spec: return_5d_t1_v1}",
                "  model: {}",
                "  portfolio: {policy: topk_dropout_v1}",
                "qlib:",
                "  dataset_dir: ''",
                "  versions_root: ''",
                "  dataset_name: isolation_cn",
                "  dataset_ref: research-current",
                "research:",
                "  num_threads: 1",
                "  backtest_account: 1000000",
                "  min_cost: 0",
                "  trade_unit: 1",
                "  promotion_thresholds: {min_observations: 5}",
                "strategy:",
                "  policy: topk_dropout_v1",
                "  topk_dropout: {topk: 3, n_drop: 1, hold_thresh: 1}",
                "portfolio:",
                "  top_n: 3",
                "  weighting: equal",
                "  max_position: 0.4",
                "  max_exposure: 0.9",
                "  max_group_exposure: 1.0",
                "  min_position: 0.0",
                "  volatility_floor: 0.01",
            ]
        ),
        encoding="utf-8",
    )
    return Settings.load(config)


def _run_cli(config: Path, *arguments: str) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, "-m", "tushare_qlib", "--config", str(config), *arguments],
        cwd=config.parent,
        check=True,
        capture_output=True,
        text=True,
    )
    for line in reversed(completed.stdout.splitlines()):
        if line.strip().startswith("{"):
            payload = json.loads(line)
            if isinstance(payload, dict):
                return payload
    raise AssertionError(f"CLI returned no JSON object: {completed.stdout[-1000:]}")


def test_standalone_formal_cli_research_and_backtest_without_platform(tmp_path: Path, monkeypatch):
    for name in (
        "QUANT_DATA_ROOT",
        "DATASET_RELEASE_ID",
        "TUSHARE_TOKEN",
        "QLIB_DATA_ROOT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PLATFORM_URL", "http://127.0.0.1:9")
    monkeypatch.setattr("tushare_qlib.settings.load_dotenv", lambda: None)
    settings = _settings(tmp_path)
    dates = _provider(tmp_path / "legacy_qlib")
    import_result = _run_cli(
        settings.config_path,
        "release",
        "import-qlib",
        "--path",
        str(tmp_path / "legacy_qlib"),
    )
    release_id = str(import_result["dataReleaseId"])
    dataset_version_id = str(import_result["datasetVersionId"])
    metadata = settings.paths.metadata
    metadata.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"cal_date": dates, "is_open": 1}).to_parquet(
        metadata / "trade_calendar.parquet", index=False
    )
    benchmark = metadata / "benchmarks"
    benchmark.mkdir()
    pd.DataFrame(
        {
            "trade_date": dates,
            "close": 3000.0 + np.arange(len(dates), dtype=float) * 0.8,
        }
    ).to_parquet(benchmark / "SH000300.parquet", index=False)

    root = Path(__file__).parents[1]
    train_result = _run_cli(
        settings.config_path,
        "train-select",
        "--train",
        str(dates[0].date()),
        str(dates[89].date()),
        "--valid",
        str(dates[90].date()),
        str(dates[109].date()),
        "--test",
        str(dates[110].date()),
        str(dates[139].date()),
        "--model-profile",
        str(root / "configs" / "model_profiles" / "ridge_golden_v1.yaml"),
        "--stage",
        "signal",
        "--artifact-level",
        "minimal",
    )
    run_dir = settings.paths.output / "research" / str(train_result["runId"])
    research_manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    prediction_snapshot = run_dir / "oos_predictions.snapshot.json"

    backtest_result = _run_cli(
        settings.config_path,
        "backtest-predictions",
        str(prediction_snapshot),
        "--topn",
        "3",
        "--n-drop",
        "1",
        "--hold-thresh",
        "1",
        "--artifact-level",
        "minimal",
    )
    backtest_dir = settings.paths.output / "research" / str(backtest_result["runId"])
    audit = _run_cli(settings.config_path, "research-audit", str(backtest_dir))

    registry = DatasetRegistry(settings.registry_path)
    dataset = registry.get_version(dataset_version_id)
    assert dataset is not None and dataset.data_release_id == release_id
    assert registry.resolve_release_alias("research-release-current") == release_id
    assert research_manifest["researchExperiment"]["data_release_id"] == release_id
    assert prediction_snapshot.is_file()
    assert (backtest_dir / "portfolio_report.parquet").is_file()
    assert audit["passed"] is True
