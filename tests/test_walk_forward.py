import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tushare_qlib.model_runtime import ModelProfile, ResolvedRuntime
from tushare_qlib.settings import Paths, Settings
from tushare_qlib import backtest_report
from tushare_qlib.walk_forward import (
    Fold,
    _aggregate_component_timings,
    _checkpoint_fingerprint,
    _rebase_reports,
    build_walk_forward_plan,
    run_walk_forward,
)


def test_walk_forward_plan_has_non_overlapping_oos_and_final_holdout():
    calendar = pd.bdate_range("2016-01-01", "2026-01-31")

    folds = build_walk_forward_plan(calendar, "2016-01-01", "2026-01-31")

    assert folds[-1].final_holdout is True
    assert len(folds) > 2
    assert all(
        len(calendar[(calendar >= fold.test[0]) & (calendar <= fold.test[1])]) < 252 for fold in folds[:-1]
    )
    assert len(calendar[(calendar >= folds[-1].test[0]) & (calendar <= folds[-1].test[1])]) >= 252
    for previous, current in zip(folds, folds[1:], strict=False):
        assert pd.Timestamp(previous.test[1]) < pd.Timestamp(current.test[0])
        assert pd.Timestamp(current.train[1]) < pd.Timestamp(current.valid[0])
        assert pd.Timestamp(current.valid[1]) < pd.Timestamp(current.test[0])


def test_rebase_reports_chains_account_across_fold_resets():
    first = pd.DataFrame(
        {
            "account": [100.0, 110.0],
            "return": [0.0, 0.1],
            "cash": [100.0, 10.0],
            "value": [0.0, 100.0],
            "total_cost": [0.0, 1.0],
            "total_turnover": [0.0, 100.0],
        },
        index=pd.to_datetime(["2026-01-05", "2026-01-06"]),
    )
    second = pd.DataFrame(
        {
            "account": [100.0, 105.0],
            "return": [0.0, 0.05],
            "cash": [100.0, 5.0],
            "value": [0.0, 100.0],
            "total_cost": [0.0, 2.0],
            "total_turnover": [0.0, 120.0],
        },
        index=pd.to_datetime(["2026-01-07", "2026-01-08"]),
    )

    combined = _rebase_reports([("rolling_00", first), ("final_holdout", second)])

    assert combined["account"].tolist() == pytest.approx([100.0, 110.0, 110.0, 115.5])
    assert combined["fold_key"].tolist() == ["rolling_00", "rolling_00", "final_holdout", "final_holdout"]


def test_aggregate_component_timings_sums_each_phase():
    manifests = [
        {"timings": {"phasesSeconds": {"train_seconds": 2.5, "backtest_seconds": 1.0}}},
        {"timings": {"phasesSeconds": {"train_seconds": 3.5, "predict_seconds": 0.5}}},
    ]

    assert _aggregate_component_timings(manifests) == {
        "train_seconds": 6.0,
        "backtest_seconds": 1.0,
        "predict_seconds": 0.5,
    }


def test_checkpoint_fingerprint_changes_with_profile_or_fold(tmp_path):
    settings = Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={"research": {"random_seed": 42}, "universe": {"instruments": "all"}},
        paths=Paths.from_root(tmp_path / "data"),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib",
    )
    fold = Fold(
        "rolling_00", ("2020-01-01", "2021-01-01"), ("2021-02-01", "2021-03-01"), ("2021-04-01", "2021-05-01")
    )

    base = _checkpoint_fingerprint(settings, fold, runtime_fingerprint="cpu", benchmark="SH000300", topn=30)
    other_device = _checkpoint_fingerprint(
        settings, fold, runtime_fingerprint="cuda", benchmark="SH000300", topn=30
    )
    changed_fold = Fold("rolling_00", fold.train, fold.valid, ("2021-04-01", "2021-06-01"))
    other_fold = _checkpoint_fingerprint(
        settings, changed_fold, runtime_fingerprint="cpu", benchmark="SH000300", topn=30
    )

    assert base != other_device
    assert base != other_fold


def test_default_three_month_walk_forward_reaches_aggregate_and_final_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = Paths.from_root(tmp_path / "data")
    paths.mkdirs()
    calendar = pd.bdate_range("2016-01-01", "2026-01-31")
    pd.DataFrame({"cal_date": calendar, "is_open": 1}).to_parquet(
        paths.metadata / "trade_calendar.parquet", index=False
    )
    settings = Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={
            "research": {
                "promotion_thresholds": {
                    "min_observations": 252,
                    "min_ic_mean": 0.01,
                    "min_rank_ic_mean": 0.02,
                    "min_icir": 0.50,
                    "min_long_short_annualized": 0.05,
                    "min_excess_ir": 0.50,
                    "max_drawdown": 0.20,
                    "require_unique_artifact": True,
                }
            },
            "universe": {"instruments": "all"},
        },
        paths=paths,
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib",
    )
    profile = ModelProfile("test", "lightgbm", "cpu", 0, {}, "test")
    runtime = ResolvedRuntime(profile, "cpu", None, {"lightgbm": "test"})
    monkeypatch.setattr("tushare_qlib.walk_forward.load_model_profile", lambda *args, **kwargs: profile)
    monkeypatch.setattr("tushare_qlib.walk_forward.resolve_runtime", lambda value: runtime)
    monkeypatch.setattr(backtest_report, "write_backtest_report", lambda *args, **kwargs: None)

    calls: list[tuple[str, str, int]] = []

    def fake_train(
        _settings: Settings,
        *,
        test: tuple[str, str],
        run_kind: str,
        promotion_mode: str,
        **kwargs,
    ) -> Path:
        del kwargs
        dates = pd.bdate_range(test[0], test[1])
        calls.append((run_kind, promotion_mode, len(dates)))
        run_id = f"run-{len(calls):02d}"
        output = paths.output / "research" / run_id
        output.mkdir(parents=True)
        index = pd.MultiIndex.from_product([dates, ["A", "B", "C", "D"]], names=["datetime", "instrument"])
        day = np.repeat(np.arange(len(dates)), 4)
        base_score = np.tile([4.0, 3.0, 2.0, 1.0], len(dates))
        predictions = pd.DataFrame({"score": base_score + np.sin(day) * 0.01}, index=index)
        labels = pd.DataFrame(
            {
                "label": np.tile([0.02, 0.01, -0.005, -0.015], len(dates))
                + np.cos(day * 0.37) * np.tile([0.001, -0.001, 0.0005, -0.0005], len(dates))
            },
            index=index,
        )
        report = pd.DataFrame(
            {
                "account": 100_000 * (1.0015 ** np.arange(1, len(dates) + 1)),
                "return": np.where(np.arange(len(dates)) % 2, 0.001, 0.002),
                "bench": 0.0,
                "cost": 0.0,
                "cash": 10_000.0,
                "value": 90_000.0,
                "total_cost": 0.0,
                "total_turnover": np.arange(len(dates), dtype=float),
            },
            index=dates,
        )
        audit = pd.DataFrame({"instrument": ["A"], "signal_date": [str(dates[-1].date())]})
        holdings = pd.DataFrame({"instrument": ["A"], "quantity": [100]})
        artifacts = {
            "oos_predictions.parquet": predictions,
            "oos_labels.parquet": labels,
            "portfolio_report.parquet": report,
            "strategy_audit.parquet": audit,
            "holdings.parquet": holdings,
        }
        entries = []
        for name, frame in artifacts.items():
            path = output / name
            frame.to_parquet(path)
            entries.append({"name": name, "localPath": str(path), "rows": len(frame)})
        manifest = {
            "schemaVersion": "2.0",
            "externalRunId": run_id,
            "dataset": {"fingerprint": "dataset-1"},
            "canonicalConfig": {},
            "lineage": {"lineageId": f"lineage-{run_id}", "complete": True},
            "promotion": {
                "status": "PROMOTED" if promotion_mode == "release" else "CANDIDATE",
                "decision": "PROMOTE" if promotion_mode == "release" else "COMPONENT_VALIDATED",
                "gateMode": "release" if promotion_mode == "release" else "component_validation",
            },
            "execution": {},
            "timings": {"phasesSeconds": {}},
            "artifacts": entries,
        }
        manifest_path = output / "manifest.json"
        if promotion_mode == "release":
            manifest["latestTargets"] = {"artifactType": "MODEL_TOPK", "targets": []}
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        if promotion_mode == "component":
            return manifest_path
        selection = output / "selection.csv"
        pd.DataFrame({"model_id": [run_id]}).to_csv(selection, index=False)
        return selection

    monkeypatch.setattr("tushare_qlib.walk_forward.train_backtest_select", fake_train)

    manifest_path = run_walk_forward(settings, start_date="2016-01-01", end_date="2026-01-31")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    rolling_calls = [call for call in calls if call[0] == "walk_forward_fold"]
    final_calls = [call for call in calls if call[0] == "final_holdout"]
    assert rolling_calls and all(
        mode == "component" and observations < 252 for _, mode, observations in rolling_calls
    )
    assert final_calls == [("final_holdout", "release", final_calls[0][2])]
    assert final_calls[0][2] >= 252
    assert manifest["promotion"]["aggregateOosGate"]["passed"] is True
    assert manifest["promotion"]["finalHoldoutGate"]["status"] == "PROMOTED"
    assert manifest["promotion"]["gateMode"] == "aggregate_oos_and_final_holdout"
