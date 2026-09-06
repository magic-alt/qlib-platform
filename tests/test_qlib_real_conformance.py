from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("QLIB_REAL_CONFORMANCE") != "1",
    reason="real Qlib conformance is isolated to the dedicated compatibility workflow",
)


def _write_feature(path: Path, values: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = np.hstack([np.float32(0), np.asarray(values, dtype=np.float32)]).astype("<f4")
    payload.tofile(path)


def _write_provider(root: Path) -> tuple[str, str]:
    dates = pd.bdate_range("2020-01-01", "2021-06-30")
    first = dates[0].strftime("%Y-%m-%d")
    last = dates[-1].strftime("%Y-%m-%d")
    stocks = [f"SH6000{index:02d}" for index in range(8)]
    benchmark = "SH000300"
    symbols = [*stocks, benchmark]

    calendars = root / "calendars"
    instruments = root / "instruments"
    features = root / "features"
    calendars.mkdir(parents=True, exist_ok=True)
    instruments.mkdir(parents=True, exist_ok=True)
    features.mkdir(parents=True, exist_ok=True)

    (calendars / "day.txt").write_text(
        "\n".join(date.strftime("%Y-%m-%d") for date in dates) + "\n",
        encoding="utf-8",
    )
    (instruments / "all.txt").write_text(
        "\n".join(f"{symbol}\t{first}\t{last}" for symbol in symbols) + "\n",
        encoding="utf-8",
    )
    (instruments / "csi300.txt").write_text(
        "\n".join(f"{symbol}\t{first}\t{last}" for symbol in stocks) + "\n",
        encoding="utf-8",
    )

    t = np.arange(len(dates), dtype=np.float64)
    for index, symbol in enumerate(symbols):
        phase = 0.37 * index
        trend = 0.00035 + index * 0.000025
        base = 12.0 + index * 1.7
        close = base * (1.0 + trend * t + 0.025 * np.sin(t / 13.0 + phase))
        open_ = close * (1.0 + 0.0025 * np.sin(t / 7.0 + phase / 2.0))
        high = np.maximum(open_, close) * (1.008 + 0.001 * np.cos(t / 11.0 + phase))
        low = np.minimum(open_, close) * (0.992 - 0.001 * np.sin(t / 9.0 + phase))
        vwap = (open_ + high + low + close) / 4.0
        volume = 800_000.0 + index * 75_000.0 + 80_000.0 * (1.0 + np.sin(t / 5.0 + phase))
        change = np.zeros_like(close)
        change[1:] = close[1:] / close[:-1] - 1.0
        factor = np.ones_like(close)

        symbol_root = features / symbol.lower()
        for field, values in {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "vwap": vwap,
            "volume": volume,
            "change": change,
            "factor": factor,
        }.items():
            _write_feature(symbol_root / f"{field}.day.bin", values)

    return first, last


def _write_workflow(path: Path, provider: Path) -> None:
    provider_text = provider.as_posix().replace("'", "''")
    path.write_text(
        f"""qlib_init:
    provider_uri: '{provider_text}'
    region: cn
    exp_manager:
        class: MLflowExpManager
        module_path: qlib.workflow.expm
        kwargs:
            uri: '{{{{ QLIB_CONFORMANCE_TRACKING_URI }}}}'
            default_exp_name: Experiment
market: &market csi300
benchmark: &benchmark SH000300
data_handler_config: &data_handler_config
    start_time: 2020-01-01
    end_time: 2021-06-25
    fit_start_time: 2020-04-01
    fit_end_time: 2020-09-30
    instruments: *market
port_analysis_config: &port_analysis_config
    strategy:
        class: TopkDropoutStrategy
        module_path: qlib.contrib.strategy
        kwargs:
            signal: <PRED>
            topk: 3
            n_drop: 1
    backtest:
        start_time: 2020-12-01
        end_time: 2021-05-28
        account: 1000000
        benchmark: *benchmark
        exchange_kwargs:
            limit_threshold: 0.095
            deal_price: close
            open_cost: 0.0001
            close_cost: 0.0001
            min_cost: 0
task:
    model:
        class: LGBModel
        module_path: qlib.contrib.model.gbdt
        kwargs:
            loss: mse
            learning_rate: 0.05
            max_depth: 3
            num_leaves: 7
            num_threads: 1
            num_boost_round: 24
            early_stopping_rounds: 6
            feature_fraction_seed: 17
            bagging_seed: 17
            data_random_seed: 17
            deterministic: true
            force_col_wise: true
    dataset:
        class: DatasetH
        module_path: qlib.data.dataset
        kwargs:
            handler:
                class: Alpha158
                module_path: qlib.contrib.data.handler
                kwargs: *data_handler_config
            segments:
                train: [2020-04-01, 2020-09-30]
                valid: [2020-10-01, 2020-11-30]
                test: [2020-12-01, 2021-05-28]
    record:
        - class: SignalRecord
          module_path: qlib.workflow.record_temp
          kwargs:
              model: <MODEL>
              dataset: <DATASET>
        - class: SigAnaRecord
          module_path: qlib.workflow.record_temp
          kwargs:
              ana_long_short: false
              ann_scaler: 252
        - class: PortAnaRecord
          module_path: qlib.workflow.record_temp
          kwargs:
              config: *port_analysis_config
""",
        encoding="utf-8",
    )


def _run(command: list[str], *, cwd: Path) -> None:
    cwd.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONHASHSEED"] = "0"
    # Qlib 0.9.7 supports an explicit MLflow experiment manager. Use its SQL
    # tracking backend so the conformance contract remains valid on current,
    # security-supported MLflow releases without reviving the deprecated file store.
    env["QLIB_CONFORMANCE_TRACKING_URI"] = f"sqlite:///{(cwd / 'tracking.db').as_posix()}"
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=420,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "command failed:\n"
            + " ".join(command)
            + f"\n--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
        )


def _single_artifact(root: Path, suffix: str) -> Path:
    matches = [path for path in root.rglob(Path(suffix).name) if path.as_posix().endswith(suffix)]
    assert len(matches) == 1, f"expected one {suffix!r} below {root}, found {matches}"
    return matches[0]


def _artifact_contract(root: Path) -> set[str]:
    contract: set[str] = set()
    for path in root.rglob("*"):
        if not path.is_file() or "artifacts" not in path.parts:
            continue
        artifact_index = path.parts.index("artifacts")
        contract.add(Path(*path.parts[artifact_index + 1 :]).as_posix())
    return contract


def test_real_qlib_097_alpha158_qrun_is_behaviorally_preserved(tmp_path: Path) -> None:
    import qlib

    assert qlib.__version__ == "0.9.7"

    provider = tmp_path / "provider"
    _write_provider(provider)
    workflow = tmp_path / "workflow_alpha158_lightgbm.yaml"
    _write_workflow(workflow, provider)

    upstream_runs = tmp_path / "upstream"
    platform_runs = tmp_path / "platform"

    _run(
        [
            sys.executable,
            "-c",
            (
                "import sys; from qlib.cli.run import workflow; "
                "workflow(sys.argv[1], experiment_name='qlib-upstream-conformance')"
            ),
            str(workflow),
        ],
        cwd=upstream_runs,
    )
    _run(
        [
            sys.executable,
            "-m",
            "qlib_platform.qlib_compat.cli",
            "run",
            str(workflow),
            "--experiment-name",
            "qlib-platform-conformance",
        ],
        cwd=platform_runs,
    )

    required_artifacts = {
        "pred.pkl",
        "label.pkl",
        "sig_analysis/ic.pkl",
        "sig_analysis/ric.pkl",
        "portfolio_analysis/report_normal_1day.pkl",
        "portfolio_analysis/positions_normal_1day.pkl",
        "portfolio_analysis/port_analysis_1day.pkl",
    }
    upstream_contract = _artifact_contract(upstream_runs)
    platform_contract = _artifact_contract(platform_runs)
    assert required_artifacts <= upstream_contract
    assert required_artifacts <= platform_contract
    assert upstream_contract == platform_contract

    for suffix in (
        "artifacts/pred.pkl",
        "artifacts/label.pkl",
        "artifacts/sig_analysis/ic.pkl",
        "artifacts/sig_analysis/ric.pkl",
        "artifacts/portfolio_analysis/report_normal_1day.pkl",
        "artifacts/portfolio_analysis/port_analysis_1day.pkl",
    ):
        upstream = pd.read_pickle(_single_artifact(upstream_runs, suffix))
        platform = pd.read_pickle(_single_artifact(platform_runs, suffix))
        if isinstance(upstream, pd.Series):
            pd.testing.assert_series_equal(upstream, platform, check_exact=False, rtol=1e-10, atol=1e-12)
        else:
            pd.testing.assert_frame_equal(upstream, platform, check_exact=False, rtol=1e-10, atol=1e-12)

    prediction = pd.read_pickle(_single_artifact(platform_runs, "artifacts/pred.pkl"))
    report = pd.read_pickle(
        _single_artifact(platform_runs, "artifacts/portfolio_analysis/report_normal_1day.pkl")
    )
    assert not prediction.empty
    assert prediction.index.names == ["datetime", "instrument"]
    assert {"account", "return", "cost", "bench"} <= set(report.columns)
    assert report.index.is_monotonic_increasing
