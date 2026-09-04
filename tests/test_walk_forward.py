import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from qlib_platform.models.model_runtime import ModelProfile, ResolvedRuntime
from qlib_platform.artifacts.prediction_snapshot import (
    PredictionSnapshotSpec,
    load_prediction_snapshot,
    prediction_snapshot_path,
    write_prediction_snapshot,
)
from qlib_platform.settings import Paths, Settings
from qlib_platform.data.store import sha256_file
import qlib_platform.backtesting.backtest_report as backtest_report
from qlib_platform.research.workflow.walk_forward import (
    Fold,
    _aggregate_component_timings,
    _checkpoint_fingerprint,
    _verify_fold_boundary_continuity,
    _write_continuous_oos_stream,
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


def test_continuous_oos_stream_rejects_overlapping_fold_dates(tmp_path: Path):
    manifests = []
    for run_id, dates in (
        ("run-1", pd.to_datetime(["2026-01-05", "2026-01-06"])),
        ("run-2", pd.to_datetime(["2026-01-06", "2026-01-07"])),
    ):
        output = tmp_path / run_id
        output.mkdir()
        index = pd.MultiIndex.from_product([dates, ["A", "B"]], names=["datetime", "instrument"])
        pred_path = output / "oos_predictions.parquet"
        label_path = output / "oos_labels.parquet"
        pd.DataFrame({"score": range(len(index))}, index=index).to_parquet(pred_path)
        pd.DataFrame({"label": range(len(index))}, index=index).to_parquet(label_path)
        manifests.append(
            {
                "externalRunId": run_id,
                "artifacts": [
                    {"name": pred_path.name, "localPath": str(pred_path)},
                    {"name": label_path.name, "localPath": str(label_path)},
                ],
            }
        )

    with pytest.raises(ValueError, match="overlap or are out of order"):
        _write_continuous_oos_stream(manifests, tmp_path / "aggregate")


def test_continuous_oos_stream_publishes_governed_aggregate_snapshot(tmp_path: Path):
    manifests = []
    for number, date in enumerate(("2026-01-05", "2026-01-06"), start=1):
        run_id = f"run-{number}"
        output = tmp_path / run_id
        output.mkdir()
        index = pd.MultiIndex.from_product(
            [pd.to_datetime([date]), ["A", "B"]], names=["datetime", "instrument"]
        )
        predictions = pd.DataFrame({"score": [0.2, 0.1]}, index=index)
        labels = pd.DataFrame({"label": [0.02, -0.01]}, index=index)
        pred_path = output / "oos_predictions.parquet"
        label_path = output / "oos_labels.parquet"
        snapshot = write_prediction_snapshot(
            pred_path,
            predictions,
            labels=labels,
            spec=PredictionSnapshotSpec(
                data_release_id="ds_test",
                alpha_pack_id="alpha158_pit_v1",
                feature_snapshot_id="fs_test",
                label_spec_id="return_5d_t1_v1",
                split_spec_id=f"split-{number}",
                model_id=f"model-{number}",
                model_profile_id="ridge_golden_v1",
                fold_id=f"rolling-{number}",
            ),
        )
        labels.to_parquet(label_path)
        manifests.append(
            {
                "externalRunId": run_id,
                "predictionSnapshot": snapshot,
                "artifacts": [
                    {"name": pred_path.name, "localPath": str(pred_path)},
                    {
                        "name": prediction_snapshot_path(pred_path).name,
                        "localPath": str(prediction_snapshot_path(pred_path)),
                    },
                    {"name": label_path.name, "localPath": str(label_path)},
                ],
            }
        )

    prediction_path, _, metadata = _write_continuous_oos_stream(manifests, tmp_path / "aggregate")
    combined, aggregate_snapshot = load_prediction_snapshot(prediction_path)

    assert len(combined) == 4
    assert aggregate_snapshot["contract"]["fold_id"] == "rolling_oos_aggregate"
    assert metadata["predictionSnapshot"] == aggregate_snapshot


def test_fold_boundary_continuity_keeps_holding_age(tmp_path: Path):
    manifests = []
    for run_id, date in (("run-1", "2026-01-06"), ("run-2", "2026-01-07")):
        output = tmp_path / run_id
        output.mkdir()
        index = pd.MultiIndex.from_product([pd.to_datetime([date]), ["A"]], names=["datetime", "instrument"])
        pred_path = output / "oos_predictions.parquet"
        pd.DataFrame({"score": [1.0]}, index=index).to_parquet(pred_path)
        manifests.append(
            {
                "externalRunId": run_id,
                "artifacts": [{"name": pred_path.name, "localPath": str(pred_path)}],
            }
        )
    holdings = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2026-01-06", "2026-01-07"]),
            "instrument": ["A", "A"],
            "quantity": [100.0, 100.0],
            "holding_days": [4, 5],
        }
    )

    result = _verify_fold_boundary_continuity(manifests, holdings, pd.DataFrame())

    assert result["passed"] is True
    assert result["boundaries"][0]["untouchedContinuingPositions"] == 1
    assert result["boundaries"][0]["unexpectedHoldingDayResets"] == []

    reset_holdings = holdings.copy()
    reset_holdings.loc[1, "holding_days"] = 1
    with pytest.raises(RuntimeError, match="holding_days reset"):
        _verify_fold_boundary_continuity(manifests, reset_holdings, pd.DataFrame())


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


def test_checkpoint_fingerprint_changes_with_strategy_or_execution(tmp_path):
    settings = Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={
            "research": {"open_cost": 0.00035},
            "strategy": {"topk_dropout": {"topk": 30, "n_drop": 5, "hold_thresh": 5}},
            "universe": {"instruments": "all"},
        },
        paths=Paths.from_root(tmp_path / "data"),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib",
    )
    fold = Fold(
        "rolling_00",
        ("2020-01-01", "2021-01-01"),
        ("2021-02-01", "2021-03-01"),
        ("2021-04-01", "2021-05-01"),
    )
    base = _checkpoint_fingerprint(settings, fold, runtime_fingerprint="cpu", benchmark="SH000300", topn=None)
    settings.data["strategy"]["topk_dropout"]["n_drop"] = 3
    strategy_changed = _checkpoint_fingerprint(
        settings, fold, runtime_fingerprint="cpu", benchmark="SH000300", topn=None
    )
    settings.data["research"]["open_cost"] = 0.0005
    execution_changed = _checkpoint_fingerprint(
        settings, fold, runtime_fingerprint="cpu", benchmark="SH000300", topn=None
    )

    assert strategy_changed != base
    assert execution_changed != strategy_changed


def test_checkpoint_fingerprint_covers_fold_artifact_producer(tmp_path, monkeypatch):
    settings = Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={"research": {"random_seed": 42}, "universe": {"instruments": "all"}},
        paths=Paths.from_root(tmp_path / "data"),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib",
    )
    fold = Fold(
        "rolling_00",
        ("2020-01-01", "2021-01-01"),
        ("2021-02-01", "2021-03-01"),
        ("2021-04-01", "2021-05-01"),
    )
    import qlib_platform.research.workflow.walk_forward as walk_forward

    original_sha256_file = walk_forward.sha256_file
    producer_hash = {"value": "producer-v1"}

    def controlled_sha256(path):
        if Path(path).name == "train_select.py":
            return producer_hash["value"]
        return original_sha256_file(path)

    monkeypatch.setattr(walk_forward, "sha256_file", controlled_sha256)
    base = _checkpoint_fingerprint(settings, fold, runtime_fingerprint="cpu", benchmark="SH000300", topn=30)
    producer_hash["value"] = "producer-v2"
    changed = _checkpoint_fingerprint(
        settings, fold, runtime_fingerprint="cpu", benchmark="SH000300", topn=30
    )

    assert changed != base


def test_default_three_month_walk_forward_completes_when_fold_and_final_quality_are_rejected(
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
    monkeypatch.setattr(
        "qlib_platform.research.workflow.walk_forward.load_model_profile", lambda *args, **kwargs: profile
    )
    monkeypatch.setattr("qlib_platform.research.workflow.walk_forward.resolve_runtime", lambda value: runtime)
    monkeypatch.setattr(
        "qlib_platform.research.workflow.walk_forward.shared_research_calendar", lambda value: calendar
    )
    monkeypatch.setattr(
        "qlib_platform.research.workflow.walk_forward.git_revision",
        lambda value: {"commit": "test-commit", "dirty": False},
    )
    monkeypatch.setattr(backtest_report, "write_backtest_report", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "qlib_platform.research.workflow.walk_forward.feature_store_enabled", lambda value: True
    )
    monkeypatch.setattr(
        "qlib_platform.research.workflow.walk_forward.prepare_feature_data",
        lambda *args, **kwargs: (
            pd.DataFrame(),
            {
                "featureSnapshotId": "fs-test",
                "cacheStatus": "REUSED",
                "rawMaterializationCalls": 0,
            },
        ),
    )

    calls: list[tuple[str, str, int]] = []

    def fake_train(
        _settings: Settings,
        *,
        train: tuple[str, str],
        valid: tuple[str, str],
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
                "account": 100_000 * (0.99 ** np.arange(1, len(dates) + 1)),
                "return": -0.01,
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
        }
        if promotion_mode == "holdout":
            artifacts.update(
                {
                    "portfolio_report.parquet": report,
                    "strategy_audit.parquet": audit,
                    "holdings.parquet": holdings,
                }
            )
        gate_path = output / ("research_gate.json" if promotion_mode == "holdout" else "component_gate.json")
        gate_path.write_text(
            json.dumps(
                {
                    "passed": False,
                    "decision": "REJECT",
                }
            ),
            encoding="utf-8",
        )
        entries = []
        for name, frame in artifacts.items():
            path = output / name
            frame.to_parquet(path)
            entries.append({"name": name, "localPath": str(path), "rows": len(frame)})
        entries.append({"name": gate_path.name, "localPath": str(gate_path)})
        manifest = {
            "schemaVersion": "2.0",
            "externalRunId": run_id,
            "dataset": {"fingerprint": "dataset-1"},
            "featureStore": {"featureSnapshotId": "fs-test"},
            "processorState": {
                "fitWindow": list(train),
                "processorStateSha256": f"processor-{run_id}",
            },
            "researchExperiment": {
                "data_release_id": "dataset-1",
                "alpha_pack_id": "alpha-test",
                "alpha_pack_sha256": "alpha-sha",
                "label_spec_id": "label-test",
                "label": {"lookahead_days": 6},
                "split_profile_id": "wf-test",
                "model_profile_id": "test",
                "model_profile_sha256": "model-sha",
                "portfolio_policy_id": "topk_dropout_v1",
                "portfolio_policy_sha256": "portfolio-sha",
            },
            "canonicalConfig": {},
            "lineage": {"lineageId": f"lineage-{run_id}", "complete": True},
            "promotion": {
                "status": "REJECTED",
                "decision": "REJECT",
                "gateMode": "final_holdout" if promotion_mode == "holdout" else "component_validation",
                "gateReportPath": str(gate_path),
                "promotionAuthorized": False,
            },
            "execution": {},
            "timings": {"phasesSeconds": {}},
            "folds": [
                {
                    "key": run_kind,
                    "train": list(train),
                    "valid": list(valid),
                    "test": list(test),
                }
            ],
            "artifacts": entries,
        }
        manifest_path = output / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return manifest_path

    monkeypatch.setattr("qlib_platform.research.workflow.walk_forward.train_backtest_select", fake_train)

    def fake_continuous_backtest(
        _settings: Settings,
        predictions: str | Path,
        **kwargs,
    ) -> Path:
        del kwargs
        pred = pd.read_parquet(predictions)
        dates = pd.DatetimeIndex(pred.index.get_level_values("datetime").unique()).sort_values()
        output = paths.output / "research" / "continuous-oos-run"
        output.mkdir(parents=True, exist_ok=True)
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
        holdings = pd.DataFrame(
            {
                "trade_date": dates,
                "instrument": "A",
                "quantity": 100.0,
                "holding_days": np.arange(1, len(dates) + 1),
            }
        )
        audit = pd.DataFrame(columns=["trade_date", "instrument", "actual_action"])
        artifacts = {
            "portfolio_report.parquet": report,
            "strategy_audit.parquet": audit,
            "holdings.parquet": holdings,
        }
        entries = []
        for name, frame in artifacts.items():
            path = output / name
            frame.to_parquet(path, index=name == "portfolio_report.parquet")
            entries.append({"name": name, "localPath": str(path), "rows": len(frame)})
        manifest = {
            "externalRunId": "continuous-oos-run",
            "timings": {"phasesSeconds": {"portfolio_engine_seconds": 1.0}},
            "artifacts": entries,
        }
        manifest_path = output / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return manifest_path

    monkeypatch.setattr(
        "qlib_platform.research.workflow.walk_forward.backtest_predictions", fake_continuous_backtest
    )

    with pytest.raises(RuntimeError, match="interrupted after fold rolling_02"):
        run_walk_forward(
            settings,
            start_date="2016-01-01",
            end_date="2026-01-31",
            acceptance_mode=True,
            interrupt_after_fold=3,
        )
    manifest_path = run_walk_forward(
        settings,
        start_date="2016-01-01",
        end_date="2026-01-31",
        acceptance_mode=True,
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    rolling_calls = [call for call in calls if call[0] == "walk_forward_fold"]
    final_calls = [call for call in calls if call[0] == "final_holdout"]
    assert rolling_calls and all(
        mode == "component" and observations < 252 for _, mode, observations in rolling_calls
    )
    assert final_calls == [("final_holdout", "holdout", final_calls[0][2])]
    assert final_calls[0][2] >= 252
    assert manifest["promotion"]["aggregateOosGate"]["passed"] is True
    assert manifest["promotion"]["finalHoldoutGate"]["passed"] is False
    assert manifest["promotion"]["gateMode"] == "aggregate_oos_and_final_holdout"
    assert manifest["aggregatePortfolioRun"]["stateMode"] == "single_continuous_account"
    assert manifest["evaluationScopes"]["topLevelPortfolioArtifacts"] == "rolling_oos_only"
    rolling_runs = [run for run in manifest["componentRuns"] if run["key"].startswith("rolling_")]
    assert rolling_runs
    assert all(run["artifactMode"] == "signal_only" for run in rolling_runs)
    assert all(run["portfolioBacktestExecuted"] is False for run in rolling_runs)
    assert manifest["walkForwardEvidence"]["checkpointRecovery"]["validFoldReuseCount"] == 3
    assert manifest["walkForwardEvidence"]["systemAcceptance"] == "PASS"
    assert manifest["walkForwardEvidence"]["researchQuality"] == "REJECT"

    artifact_paths = {
        item["name"]: Path(item["localPath"])
        for item in manifest["artifacts"]
        if item["name"] in {"oos_predictions.parquet", "portfolio_report.parquet", "holdings.parquet"}
    }
    exact_before = {name: sha256_file(path) for name, path in artifact_paths.items()}
    replay_path = run_walk_forward(
        settings,
        start_date="2016-01-01",
        end_date="2026-01-31",
        acceptance_mode=True,
    )
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    exact_after = {
        item["name"]: sha256_file(Path(item["localPath"]))
        for item in replay["artifacts"]
        if item["name"] in exact_before
    }
    assert exact_after == exact_before
