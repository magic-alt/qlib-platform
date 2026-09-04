from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from qlib_platform.lineage import sha256_json
from qlib_platform.artifacts.prediction_snapshot import PredictionSnapshotSpec, write_prediction_snapshot
from qlib_platform.research.studies.attribution import run_attribution_diagnose
from qlib_platform.research.diagnostics.failure_attribution import (
    derive_daily_model_topk_overlap,
    derive_daily_signal_conversion,
    derive_failure_summary,
    load_failure_attribution_spec,
    summarize_model_topk_overlap,
    summarize_signal_conversion,
)
from qlib_platform.research.diagnostics.portfolio_attribution import (
    build_daily_holdings_conversion,
    build_daily_portfolio_bridge,
    derive_benchmark_diagnostics,
    derive_cost_sensitivity,
    derive_rolling_benchmark_diagnostics,
    summarize_portfolio_bridge,
)
from qlib_platform.research.diagnostics.turnover_attribution import derive_turnover_attribution
from qlib_platform.settings import Paths, Settings
from qlib_platform.data.store import sha256_file


def _panel() -> tuple[dict[str, pd.DataFrame], pd.DataFrame, dict[pd.Timestamp, str]]:
    dates = pd.bdate_range("2026-01-05", periods=4)
    instruments = ["A", "B", "C", "D", "E"]
    index = pd.MultiIndex.from_product([dates, instruments], names=["datetime", "instrument"])
    labels = pd.DataFrame({"label": np.tile([0.05, 0.03, 0.01, -0.01, -0.03], 4)}, index=index)
    xgb = np.tile([5.0, 4.0, 3.0, 2.0, 1.0], 4)
    predictions = {
        "xgboost": pd.DataFrame({"score": xgb}, index=index),
        "lightgbm": pd.DataFrame({"score": xgb}, index=index),
        "ridge": pd.DataFrame({"score": np.tile([1.0, 2.0, 3.0, 4.0, 5.0], 4)}, index=index),
    }
    folds = {date: ("rolling_06" if number < 2 else "rolling_07") for number, date in enumerate(dates)}
    return predictions, labels, folds


def _regimes() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.bdate_range("2026-01-05", periods=4),
            "dimension": "market_volatility",
            "state": ["LOW", "LOW", "HIGH", "HIGH"],
            "status": "AVAILABLE",
        }
    )


def _spec():
    return load_failure_attribution_spec("configs/attribution/ashare_failure_attribution_v1.yaml")


def test_signal_conversion_separates_rankic_topk_and_temporal_turnover():
    predictions, labels, folds = _panel()

    daily = derive_daily_signal_conversion(
        predictions,
        labels,
        topk=2,
        minimum_cross_section=5,
        fold_assignments=folds,
    )
    summary = summarize_signal_conversion(daily, _regimes())
    overlap = derive_daily_model_topk_overlap(
        predictions,
        labels,
        topk=2,
        minimum_cross_section=5,
        fold_assignments=folds,
    )
    overlap_summary = summarize_model_topk_overlap(overlap, _regimes())

    xgb = summary.loc[summary["scope_type"].eq("ALL_OOS") & summary["model"].eq("xgboost")].iloc[0]
    xgb_lgb = overlap_summary.loc[
        overlap_summary["scope_type"].eq("ALL_OOS") & overlap_summary["pair"].eq("xgboost_vs_lightgbm")
    ].iloc[0]
    assert xgb["rank_ic"] == pytest.approx(1.0)
    assert xgb["topk_mean_label"] == pytest.approx(0.04)
    assert xgb["topk_minus_universe"] == pytest.approx(0.03)
    assert xgb["topk_overlap_previous"] == pytest.approx(1.0)
    assert xgb["rank_turnover"] == pytest.approx(0.0)
    assert xgb_lgb["overlap_ratio_mean"] == pytest.approx(1.0)


def _audit() -> pd.DataFrame:
    signal_dates = pd.bdate_range("2026-01-05", periods=4)
    trade_dates = pd.bdate_range("2026-01-06", periods=4)
    return pd.DataFrame(
        {
            "signal_date": signal_dates,
            "trade_date": trade_dates,
            "instrument": ["A", "B", "C", "D"],
            "target_action": ["BUY", "SELL", "HOLD", "BUY"],
            "action_reason": [
                "NEW_ENTRY",
                "DROP_LOWEST_COMBINED_SCORE",
                "HOLD_THRESHOLD_NOT_MET",
                "TOPK_FILL_OR_REPLACEMENT",
            ],
            "quantity_before": [0.0, 100.0, 100.0, 0.0],
            "quantity_after": [100.0, 0.0, 100.0, 50.0],
            "order_requested": [True, True, False, True],
            "requested_quantity": [100.0, 100.0, 0.0, 100.0],
            "filled_quantity": [100.0, 0.0, 0.0, 50.0],
            "filled_value": [1_000.0, 0.0, 0.0, 500.0],
            "trade_cost": [1.0, 0.0, 0.0, 0.5],
            "actual_action": ["BUY", "HOLD", "HOLD", "BUY"],
            "execution_status": ["FILLED", "UNFILLED", "NOT_REQUESTED", "PARTIAL"],
            "paused": [False, False, False, False],
            "is_limit_up": [False, False, False, False],
            "is_limit_down": [False, True, False, False],
            "candidate_tradable": [True, False, True, True],
        }
    )


def test_portfolio_and_cost_chain_reuses_realized_gross_path():
    predictions, _, folds = _panel()
    xgb = predictions["xgboost"]
    trade_dates = pd.bdate_range("2026-01-06", periods=4)
    report = pd.DataFrame(
        {
            "return": [0.01, -0.01, 0.02, -0.02],
            "bench": [0.002, 0.001, -0.001, 0.0],
            "cost": [0.001, 0.001, 0.001, 0.001],
            "turnover": [0.1, 0.2, 0.3, 0.4],
        },
        index=trade_dates,
    )
    holdings = pd.DataFrame(
        {
            "trade_date": np.repeat(trade_dates, 2),
            "instrument": ["A", "B"] * 4,
            "weight": [0.45, 0.45] * 4,
            "holding_days": [1, 1, 2, 2, 3, 3, 4, 4],
        }
    )

    daily = build_daily_portfolio_bridge(report, xgb, _audit(), fold_assignments=folds)
    holding_daily = build_daily_holdings_conversion(
        holdings,
        xgb,
        _audit(),
        topk=2,
        fold_assignments=folds,
    )
    portfolio = summarize_portfolio_bridge(
        daily,
        holding_daily,
        _regimes(),
        run_name="xgboost_baseline",
        model="xgboost",
        variant="baseline",
        spec=_spec(),
    )
    sensitivity = derive_cost_sensitivity(
        daily,
        _regimes(),
        run_name="xgboost_baseline",
        model="xgboost",
        variant="baseline",
        spec=_spec(),
    )

    overall = portfolio.loc[portfolio["scope_type"].eq("ALL_OOS")].iloc[0]
    zero = sensitivity.loc[
        sensitivity["scope_type"].eq("ALL_OOS") & sensitivity["cost_multiplier"].eq(0.0)
    ].iloc[0]
    doubled = sensitivity.loc[
        sensitivity["scope_type"].eq("ALL_OOS") & sensitivity["cost_multiplier"].eq(2.0)
    ].iloc[0]
    assert overall["realized_topk_overlap"] == pytest.approx(1.0)
    assert overall["annual_turnover"] == pytest.approx(63.0)
    assert zero["gross_return"] == pytest.approx(zero["net_return"])
    assert doubled["net_return"] < zero["net_return"]
    assert set(sensitivity["cost_multiplier"]) == {0.0, 0.5, 1.0, 1.5, 2.0}


def test_benchmark_diagnostics_report_beta_tracking_error_and_captures():
    predictions, _, folds = _panel()
    xgb = predictions["xgboost"]
    trade_dates = pd.bdate_range("2026-01-06", periods=4)
    report = pd.DataFrame(
        {
            "return": [0.02, -0.01, 0.03, -0.02],
            "bench": [0.01, -0.005, 0.015, -0.01],
            "cost": [0.001, 0.001, 0.001, 0.001],
            "turnover": [0.1, 0.2, 0.3, 0.4],
        },
        index=trade_dates,
    )

    daily = build_daily_portfolio_bridge(report, xgb, _audit(), fold_assignments=folds)
    diagnostics = derive_benchmark_diagnostics(
        daily,
        _regimes(),
        run_name="xgboost_baseline",
        model="xgboost",
        variant="baseline",
        spec=_spec(),
    )

    overall = diagnostics.loc[diagnostics["scope_type"].eq("ALL_OOS")].iloc[0]
    assert overall["portfolio_beta"] == pytest.approx(2.0)
    assert overall["up_capture"] == pytest.approx(2.0)
    assert overall["down_capture"] == pytest.approx(2.0)
    assert overall["gross_active_return"] > 0
    assert overall["net_active_return"] < overall["gross_active_return"]
    assert np.isfinite(overall["tracking_error"])


def test_rolling_benchmark_diagnostics_tracks_beta_and_excess_over_window():
    predictions, _, folds = _panel()
    xgb = predictions["xgboost"]
    trade_dates = pd.bdate_range("2026-01-06", periods=4)
    report = pd.DataFrame(
        {
            "return": [0.02, -0.01, 0.03, -0.02],
            "bench": [0.01, -0.005, 0.015, -0.01],
            "cost": [0.001, 0.001, 0.001, 0.001],
            "turnover": [0.1, 0.2, 0.3, 0.4],
        },
        index=trade_dates,
    )

    daily = build_daily_portfolio_bridge(report, xgb, _audit(), fold_assignments=folds)
    rolling = derive_rolling_benchmark_diagnostics(
        daily,
        run_name="xgboost_baseline",
        model="xgboost",
        variant="baseline",
        window=4,
    )

    assert set(rolling.columns) == {
        "trade_date",
        "signal_date",
        "fold",
        "run",
        "model",
        "variant",
        "rolling_beta",
        "rolling_excess_return",
        "rolling_window_days",
    }
    assert rolling["rolling_window_days"].eq(4).all()
    assert len(rolling) == len(daily)
    assert rolling["rolling_beta"].iloc[-1] == pytest.approx(2.0)
    assert rolling["rolling_excess_return"].iloc[-1] > 0


def test_turnover_attribution_reuses_strategy_audit_decisions_and_execution_outcomes():
    _, _, folds = _panel()

    result = derive_turnover_attribution(
        _audit(),
        _regimes(),
        fold_assignments=folds,
        run_name="xgboost_baseline",
        model="xgboost",
        variant="baseline",
    )

    overall = result.loc[result["scope_type"].eq("ALL_OOS")]
    assert {"ENTRY", "RANK_REPLACEMENT", "HOLD_THRESHOLD"}.issubset(set(overall["category"]))
    assert {"FILLED", "LIMIT_BLOCKED", "PARTIAL_FILL"}.issubset(set(overall["category"]))
    decision = overall.loc[overall["category_type"].eq("DECISION")]
    execution = overall.loc[overall["category_type"].eq("EXECUTION")]
    assert decision["turnover_contribution"].sum() == pytest.approx(1.0)
    assert execution["cost_contribution"].sum() == pytest.approx(1.0)


def test_failure_summary_can_identify_cost_as_primary_without_retraining():
    spec = _spec()
    signal = pd.DataFrame(
        [
            {
                "scope_type": "ALL_OOS",
                "scope": "ALL_OOS",
                "model": model,
                "rank_ic": rank_ic,
                "topk_minus_universe": topk,
                "topk_minus_bottomk": 0.03,
            }
            for model, rank_ic, topk in (
                ("ridge", 0.01, 0.005),
                ("lightgbm", 0.02, 0.01),
                ("xgboost", 0.03, 0.012),
            )
        ]
        + [
            {
                "scope_type": "FOLD",
                "scope": "rolling_07",
                "model": "xgboost",
                "rank_ic": 0.01,
                "topk_minus_universe": 0.005,
                "topk_minus_bottomk": 0.01,
            }
        ]
    )
    overlap = pd.DataFrame(
        [
            {
                "scope_type": "ALL_OOS",
                "scope": "ALL_OOS",
                "pair": "xgboost_vs_lightgbm",
                "overlap_ratio_mean": 0.5,
            }
        ]
    )
    portfolio = pd.DataFrame(
        [
            {
                "scope_type": "ALL_OOS",
                "scope": "ALL_OOS",
                "model": "xgboost",
                "variant": "baseline",
                "gross_excess_return": 0.04,
                "net_excess_return": -0.01,
                "cost_return": 0.05,
            },
            {
                "scope_type": "FOLD",
                "scope": "rolling_07",
                "model": "xgboost",
                "variant": "baseline",
                "gross_excess_return": 0.01,
                "net_excess_return": -0.01,
                "cost_return": 0.02,
            },
        ]
    )
    cost = pd.DataFrame(
        [
            {
                "scope_type": "ALL_OOS",
                "scope": "ALL_OOS",
                "model": "xgboost",
                "variant": "baseline",
                "cost_multiplier": multiplier,
                "net_excess_return": value,
            }
            for multiplier, value in ((0.0, 0.04), (1.0, -0.01))
        ]
    )

    summary = derive_failure_summary(signal, overlap, portfolio, cost, spec=spec)

    assert summary["costLayer"]["status"] == "PRIMARY"
    assert summary["primaryAlphaLossSource"] == "COST"


def test_attribution_config_keeps_bounded_variants_and_cost_grid():
    spec = load_failure_attribution_spec(Path("configs/attribution/ashare_failure_attribution_v1.yaml"))

    assert (spec.baseline.topk, spec.baseline.n_drop, spec.baseline.hold_threshold) == (30, 5, 5)
    assert spec.cost_multipliers == (0.0, 0.5, 1.0, 1.5, 2.0)


def test_attribution_study_publishes_immutable_read_only_bundle(tmp_path: Path):
    predictions, labels, _ = _panel()
    run = tmp_path / "xgboost-run"
    run.mkdir()
    xgb_path = run / "oos_predictions.parquet"
    write_prediction_snapshot(
        xgb_path,
        predictions["xgboost"],
        labels=labels,
        spec=PredictionSnapshotSpec(
            data_release_id="ds-test",
            alpha_pack_id="alpha158_pit_v1",
            feature_snapshot_id="fs-test",
            label_spec_id="return_5d_t1_v1",
            split_spec_id="wf-test",
            model_id="xgb-test",
            model_profile_id="xgboost_cpu_v1",
            fold_id="rolling_oos",
        ),
    )
    labels.to_parquet(run / "oos_labels.parquet")
    ridge_path = tmp_path / "ridge.parquet"
    lightgbm_path = tmp_path / "lightgbm.parquet"
    predictions["ridge"].to_parquet(ridge_path)
    predictions["lightgbm"].to_parquet(lightgbm_path)
    trade_dates = pd.bdate_range("2026-01-06", periods=4)
    pd.DataFrame(
        {
            "return": [0.01, -0.01, 0.02, -0.02],
            "bench": [0.002, 0.001, -0.001, 0.0],
            "cost": [0.001, 0.001, 0.001, 0.001],
            "turnover": [0.1, 0.2, 0.3, 0.4],
        },
        index=trade_dates,
    ).to_parquet(run / "portfolio_report.parquet")
    pd.DataFrame(
        {
            "trade_date": np.repeat(trade_dates, 2),
            "instrument": ["A", "B"] * 4,
            "weight": [0.45, 0.45] * 4,
            "holding_days": [1, 1, 2, 2, 3, 3, 4, 4],
        }
    ).to_parquet(run / "holdings.parquet", index=False)
    _audit().to_parquet(run / "strategy_audit.parquet", index=False)
    dates = pd.bdate_range("2026-01-05", periods=4)
    split = {
        "profile": "wf-test",
        "sha256": "split-sha",
        "folds": [
            {
                "key": "rolling_06",
                "test": [str(dates[0].date()), str(dates[1].date())],
                "final_holdout": False,
            },
            {
                "key": "rolling_07",
                "test": [str(dates[2].date()), str(dates[3].date())],
                "final_holdout": False,
            },
            {
                "key": "final_holdout",
                "test": ["2026-02-02", "2026-02-27"],
                "final_holdout": True,
            },
        ],
    }
    selection_lock = {
        "dataRelease": "ds-test",
        "alphaPack": {"id": "alpha158_pit_v1", "sha256": "alpha-sha"},
        "labelSpec": {"id": "return_5d_t1_v1", "contract": {"lookahead": 6}},
        "splitSpec": split,
        "portfolioPolicy": {"id": "topk_dropout_v1", "sha256": "portfolio-sha"},
        "gateThresholds": {},
        "codeCommit": "commit",
        "codeDirty": False,
        "finalHoldout": {"usedForResearchSelection": False},
    }
    selection_lock["lockSha256"] = sha256_json(selection_lock)
    evidence = {
        "systemAcceptance": "PASS",
        "walkForwardIntegrity": "PASS",
        "researchQuality": "REVIEW",
        "researchSelectionLock": selection_lock,
        "featureSnapshot": {
            "featureSnapshotId": "fs-test",
            "datasetVersionId": "dataset-test",
            "manifestSha256": "feature-sha",
            "rawMaterializationCalls": 0,
        },
        "oosPrediction": {
            "startDate": str(dates.min().date()),
            "endDate": str(dates.max().date()),
            "predictionDates": 4,
        },
        "model": {"family": "xgboost", "profile": "xgboost_cpu_v1"},
    }
    (run / "research_selection_lock.json").write_text(json.dumps(selection_lock), encoding="utf-8")
    (run / "walk_forward_evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
    artifacts = [
        {
            "name": path.name,
            "localPath": str(path),
        }
        for path in (
            xgb_path,
            run / "oos_predictions.snapshot.json",
            run / "oos_labels.parquet",
            run / "portfolio_report.parquet",
            run / "holdings.parquet",
            run / "strategy_audit.parquet",
        )
    ]
    (run / "manifest.json").write_text(
        json.dumps(
            {
                "walkForwardEvidence": evidence,
                "execution": {"topkDropout": {"topk": 30, "n_drop": 5, "hold_thresh": 5}},
                "artifacts": artifacts,
            }
        ),
        encoding="utf-8",
    )
    model_hashes = {
        "ridge": sha256_file(ridge_path),
        "lightgbm": sha256_file(lightgbm_path),
        "xgboost": sha256_file(xgb_path),
    }
    acceptance = {
        "acceptanceType": "FULL_WALK_FORWARD_V1",
        "systemAcceptance": "PASS",
        "walkForwardAcceptance": "PASS",
        "data": {"dataRelease": "ds-test"},
        "featureSnapshot": {
            "featureSnapshotId": "fs-test",
            "rawMaterializationCalls": 0,
        },
        "finalHoldout": {"isolated": True, "usedForResearchSelection": False},
        "determinism": {"ridge": "EXACT", "lightgbm": "EXACT", "xgboost": "EXACT"},
        "models": {
            model: {
                "predictionSha256": model_hashes[model],
                "resumedExact": (
                    {
                        "portfolio_report.parquet": sha256_file(run / "portfolio_report.parquet"),
                        "holdings.parquet": sha256_file(run / "holdings.parquet"),
                    }
                    if model == "xgboost"
                    else {}
                ),
            }
            for model in ("ridge", "lightgbm", "xgboost")
        },
    }
    acceptance_path = tmp_path / "full_walk_forward_acceptance.json"
    acceptance_path.write_text(json.dumps(acceptance), encoding="utf-8")
    regime_dir = tmp_path / "regime"
    regime_dir.mkdir()
    regime_labels_path = regime_dir / "regime_labels.parquet"
    _regimes().to_parquet(regime_labels_path, index=False)
    regime_manifest = {
        "schemaVersion": "alpha_regime_study_v1",
        "studyId": "ard-test",
        "status": {"regimeDiagnostics": "PARTIAL"},
        "selectionUsesFinalHoldout": False,
        "publishingAuthorized": False,
        "contract": {
            "fullWalkForwardAcceptanceSha256": sha256_file(acceptance_path),
            "modelPredictionSha256": model_hashes,
        },
        "artifacts": [
            {
                "name": regime_labels_path.name,
                "path": regime_labels_path.name,
                "sha256": sha256_file(regime_labels_path),
            }
        ],
    }
    regime_manifest_path = regime_dir / "regime_diagnostics_manifest.json"
    regime_manifest_path.write_text(json.dumps(regime_manifest), encoding="utf-8")
    attribution_config = yaml.safe_load(
        Path("configs/attribution/ashare_failure_attribution_v1.yaml").read_text(encoding="utf-8")
    )
    attribution_config["minimumCrossSection"] = 5
    attribution_path = tmp_path / "attribution.yaml"
    attribution_path.write_text(yaml.safe_dump(attribution_config), encoding="utf-8")
    settings = Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={},
        paths=Paths.from_root(tmp_path / "data"),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib",
    )

    manifest_path = run_attribution_diagnose(
        settings,
        regime_study=regime_manifest_path,
        acceptance=acceptance_path,
        walk_forward=run,
        ridge_predictions=ridge_path,
        lightgbm_predictions=lightgbm_path,
        attribution_path=attribution_path,
        output_root=tmp_path / "output",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["status"] == {
        "systemIntegrity": "PASS",
        "regimeDiagnostics": "PARTIAL",
        "failureAttribution": "PASS",
    }
    assert manifest["executionIsolation"] == {
        "modelTrainCalls": 0,
        "modelPredictCalls": 0,
        "featureMaterializationCalls": 0,
        "portfolioBacktestCalls": 0,
    }
    assert manifest["selectionUsesFinalHoldout"] is False
    assert manifest["publishingAuthorized"] is False
    assert {artifact["name"] for artifact in manifest["artifacts"]}.issuperset(
        {
            "prediction_portfolio_attribution.parquet",
            "turnover_attribution.parquet",
            "cost_sensitivity.parquet",
            "failure_attribution_summary.json",
        }
    )
