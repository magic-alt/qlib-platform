from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from tushare_qlib.cli import parser
from tushare_qlib.lineage import sha256_json
from tushare_qlib.processor_state import processor_state_manifest
from tushare_qlib.research.explanation_study import (
    FoldModelInput,
    _materialize_bundle,
    _model_scores_and_shap,
    _replay_processors,
    _resolve_recorder_artifacts,
    _universe_filter,
)
from tushare_qlib.research.factor_taxonomy import FactorTaxonomy, FactorTaxonomyEntry
from tushare_qlib.research.model_explanation import (
    derive_explanation_stability,
    derive_model_explanation_summary,
    derive_ridge_importance,
    derive_tree_importance,
    derive_xgb_interactions,
    deterministic_sample_positions,
    load_model_explanation_spec,
    shap_summary_rows,
)


FEATURES = ("VAL", "VOL", "MOM", "SIZE")


def _taxonomy() -> FactorTaxonomy:
    entries = {
        "VAL": FactorTaxonomyEntry("VAL", "Value", "alpha", "negative"),
        "VOL": FactorTaxonomyEntry("VOL", "Volatility", "alpha", "negative"),
        "MOM": FactorTaxonomyEntry("MOM", "Momentum", "alpha", "positive"),
        "SIZE": FactorTaxonomyEntry("SIZE", "Size", "exposure", "negative"),
    }
    return FactorTaxonomy("test", "alpha158_pit_v1", entries, sha256_json({}), "file")


def _spec():
    return load_model_explanation_spec("configs/explanation/ashare_model_explanation_v1.yaml")


def _features() -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-05", periods=3)
    instruments = [f"S{value:02d}" for value in range(8)]
    index = pd.MultiIndex.from_product([dates, instruments], names=["datetime", "instrument"])
    rng = np.random.default_rng(42)
    return pd.DataFrame(rng.normal(size=(len(index), len(FEATURES))), index=index, columns=FEATURES)


def test_repository_explanation_config_predeclares_hypotheses_and_bounds():
    spec = _spec()

    assert spec.top_features == 20
    assert spec.minimum_regime_sessions == 63
    assert spec.interaction_rows_per_fold == 128
    assert set(spec.h2_interaction_family_pairs) == {
        ("Value", "Volatility"),
        ("Size", "Value"),
        ("Momentum", "Volatility"),
    }
    assert spec.score_parity_tolerance == pytest.approx(1e-6)
    assert spec.shap_additivity_tolerance == pytest.approx(1e-5)


def test_tree_importance_ridge_reference_and_native_shap_are_well_formed():
    xgb = pytest.importorskip("xgboost")
    lgb = pytest.importorskip("lightgbm")
    features = _features()
    label = features["VAL"] * features["VOL"] + 0.2 * features["MOM"]
    xgb_booster = xgb.train(
        {"objective": "reg:squarederror", "max_depth": 2, "eta": 0.2, "seed": 42},
        xgb.DMatrix(features.to_numpy(), label=label.to_numpy()),
        num_boost_round=8,
    )
    lgb_booster = lgb.train(
        {
            "objective": "regression",
            "num_leaves": 7,
            "min_data_in_leaf": 2,
            "verbosity": -1,
            "seed": 42,
        },
        lgb.Dataset(features.to_numpy(), label=label.to_numpy()),
        num_boost_round=8,
    )

    xgb_scores, xgb_shap, _ = _model_scores_and_shap(SimpleNamespace(model=xgb_booster), "xgboost", features)
    lgb_scores, lgb_shap, _ = _model_scores_and_shap(SimpleNamespace(model=lgb_booster), "lightgbm", features)
    assert xgb_shap is not None and lgb_shap is not None
    np.testing.assert_allclose(xgb_shap.sum(axis=1), xgb_scores, atol=1e-5)
    np.testing.assert_allclose(lgb_shap.sum(axis=1), lgb_scores, atol=1e-9)

    xgb_importance = derive_tree_importance(
        xgb_booster,
        model="xgboost",
        fold="rolling_00",
        feature_names=FEATURES,
        taxonomy=_taxonomy(),
    )
    lgb_importance = derive_tree_importance(
        lgb_booster,
        model="lightgbm",
        fold="rolling_00",
        feature_names=FEATURES,
        taxonomy=_taxonomy(),
    )
    ridge = derive_ridge_importance(
        np.asarray([1.0, -2.0, 0.5, 0.0]),
        fold="rolling_00",
        feature_names=FEATURES,
        taxonomy=_taxonomy(),
    )
    assert set(xgb_importance["importance_type"]) == {"gain", "split"}
    assert set(lgb_importance["importance_type"]) == {"gain", "split"}
    assert set(ridge["importance_type"]) == {"coefficient"}
    assert ridge.loc[ridge["feature"].eq("VOL"), "rank"].iloc[0] == 1


def test_interaction_sampling_is_deterministic_symmetric_and_excludes_diagonal():
    features = _features()
    first = deterministic_sample_positions(features.index, count=7, seed=42, namespace="rolling_00")
    second = deterministic_sample_positions(features.index, count=7, seed=42, namespace="rolling_00")
    np.testing.assert_array_equal(first, second)
    values = np.zeros((7, len(FEATURES), len(FEATURES)), dtype=float)
    values[:, 0, 1] = values[:, 1, 0] = 2.0
    values[:, 0, 3] = values[:, 3, 0] = 1.0

    result = derive_xgb_interactions(
        values,
        fold="rolling_00",
        feature_names=FEATURES,
        taxonomy=_taxonomy(),
        top_pairs=6,
        observations=7,
        sessions=2,
    )

    assert len(result) == 6
    assert (result["feature_1"] != result["feature_2"]).all()
    assert result.iloc[0][["feature_1", "feature_2"]].tolist() == ["VAL", "VOL"]
    assert result.iloc[0]["mean_abs_pair_interaction"] == pytest.approx(4.0)


def test_stability_and_hypotheses_can_identify_fold_instability():
    features = _features()
    rows: list[dict[str, object]] = []
    for fold, signs in (("rolling_00", [1, 1, 1, 1]), ("rolling_01", [-1, -1, -1, 1])):
        values = features.to_numpy() * np.asarray(signs)
        rows.extend(
            shap_summary_rows(
                features,
                values,
                model="xgboost",
                scope_type="FOLD",
                scope=fold,
                fold=fold,
                taxonomy=_taxonomy(),
                additivity_max_abs_error=0.0,
            )
        )
    shap_by_fold = pd.DataFrame(rows)
    summary = shap_by_fold.groupby(["model", "feature"], as_index=False).first()
    summary["normalized_mean_abs_shap"] = 0.25
    interactions = pd.DataFrame(
        [
            {
                "fold": fold,
                "feature_1": "VAL",
                "feature_2": "VOL",
                "family_1": "Value",
                "family_2": "Volatility",
                "normalized_share": 0.4,
            }
            for fold in ("rolling_00", "rolling_01")
        ]
    )
    spec = _spec()
    stability = derive_explanation_stability(
        shap_by_fold,
        pd.DataFrame(),
        interactions,
        spec=spec,
    )
    result = derive_model_explanation_summary(
        summary,
        interactions,
        stability,
        spec=spec,
        regime_conditioning="PARTIAL",
    )

    assert result["hypotheses"]["H1"]["status"] == "SUPPORTED"
    assert result["hypotheses"]["H2"]["status"] == "SUPPORTED"
    assert result["hypotheses"]["H3"]["status"] == "SUPPORTED"
    assert result["xgbPrimaryMechanism"] == "MIXED"
    assert result["regimeImportanceDrift"] == "INPUT_PARTIAL"
    assert result["boundedSensitivity"] == "NOT_RUN_NO_RETRAIN_AUTHORIZED"


def test_explanation_bundle_is_immutable_and_detects_tamper(tmp_path: Path):
    contract = {
        "schemaVersion": "alpha_model_explanation_study_v1",
        "input": "test",
        "explanationEvaluationCalls": 1,
    }
    frames = {"feature_importance.parquet": pd.DataFrame({"feature": ["VAL"], "rank": [1]})}
    summary = {"xgbPrimaryMechanism": "INCONCLUSIVE"}

    first = _materialize_bundle(
        tmp_path,
        contract=contract,
        frames=frames,
        summary=summary,
        regime_status="PARTIAL",
    )
    reused = _materialize_bundle(
        tmp_path,
        contract=contract,
        frames=frames,
        summary=summary,
        regime_status="PARTIAL",
    )
    manifest = json.loads(first.read_text(encoding="utf-8"))

    assert reused == first
    assert manifest["modelArtifactCertification"] == "DERIVED_SAME_RECORDER_ADDITIVITY"
    assert manifest["selectionUsesFinalHoldout"] is False
    assert manifest["publishingAuthorized"] is False
    assert manifest["executionIsolation"]["modelTrainCalls"] == 0
    artifact = first.parent / "feature_importance.parquet"
    artifact.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="checksum mismatch"):
        _materialize_bundle(
            tmp_path,
            contract=contract,
            frames=frames,
            summary=summary,
            regime_status="PARTIAL",
        )


def test_processor_replay_matches_fold_state_and_model_resolution_is_unique(tmp_path: Path):
    from qlib.data.dataset.processor import CSRankNorm, DropnaLabel, Fillna, RobustZScoreNorm
    from tushare_qlib.processors import ProcessInfSingleThread

    dates = pd.bdate_range("2026-01-05", periods=4)
    instruments = ["A", "B", "C"]
    index = pd.MultiIndex.from_product([dates, instruments], names=["datetime", "instrument"])
    feature_values = pd.DataFrame(
        {
            "VAL": np.arange(len(index), dtype=float),
            "PAUSED": 0.0,
            "LISTED_DAYS": 500.0,
            "CIRC_MV": 5_000_000_000.0,
            "MONEY20": 50_000_000.0,
            "IS_ST": 0.0,
        },
        index=index,
    )
    raw = pd.concat({"feature": feature_values}, axis=1)
    filters = {
        "min_listed_days": 120,
        "min_circ_mv_yuan": 2_000_000_000,
        "min_money_20d_yuan": 20_000_000,
        "exclude_st": True,
        "allow_unknown_st": False,
    }
    shared = _universe_filter(filters)
    inference = ProcessInfSingleThread()
    normalizer = RobustZScoreNorm(
        fit_start_time=str(dates[0].date()),
        fit_end_time=str(dates[1].date()),
        fields_group="feature",
        clip_outlier=True,
    )
    fillna = Fillna(fields_group="feature")
    filtered = shared(raw.copy())
    inferred = inference(filtered.copy())
    normalizer.fit(inferred)
    state = processor_state_manifest(
        SimpleNamespace(
            shared_processors=[shared],
            infer_processors=[inference, normalizer, fillna],
            learn_processors=[DropnaLabel(), CSRankNorm(fields_group="label")],
        ),
        (str(dates[0].date()), str(dates[1].date())),
    )
    recorder = tmp_path / "mlruns" / "1" / "run-1" / "artifacts"
    recorder.mkdir(parents=True)
    (recorder / "params.pkl").write_bytes(b"model")
    (recorder / "pred.pkl").write_bytes(b"predictions")
    model_path, prediction_path = _resolve_recorder_artifacts("run-1", [tmp_path / "mlruns"])
    record = FoldModelInput(
        model="ridge",
        fold="rolling_00",
        run_id="run-1",
        train=(str(dates[0].date()), str(dates[1].date())),
        test=(str(dates[2].date()), str(dates[3].date())),
        component_manifest_path=tmp_path / "manifest.json",
        component_prediction_path=tmp_path / "predictions.parquet",
        recorder_model_path=model_path,
        recorder_prediction_path=prediction_path,
        processor_state_sha256=str(state["processorStateSha256"]),
    )
    component = {"canonicalConfig": {"dataset": {"secondary_filters": filters}}}

    replayed = _replay_processors(raw, record, component)

    assert replayed.index.equals(raw.index)
    assert np.isfinite(replayed.to_numpy(dtype=float)).all()
    tampered = FoldModelInput(**{**record.__dict__, "processor_state_sha256": "tampered"})
    with pytest.raises(ValueError, match="processor replay state differs"):
        _replay_processors(raw, tampered, component)


def test_explanation_cli_requires_three_walk_forward_bundles_and_model_root():
    args = parser().parse_args(
        [
            "explanation-diagnose",
            "--base-study",
            "base.json",
            "--regime-study",
            "regime.json",
            "--attribution-study",
            "attribution.json",
            "--acceptance",
            "acceptance.json",
            "--ridge-walk-forward",
            "ridge",
            "--lightgbm-walk-forward",
            "lightgbm",
            "--xgboost-walk-forward",
            "xgboost",
            "--feature-snapshot",
            "feature",
            "--model-artifact-root",
            "mlruns",
        ]
    )

    assert args.command == "explanation-diagnose"
    assert args.model_artifact_root == ["mlruns"]
    assert args.explanation == "configs/explanation/ashare_model_explanation_v1.yaml"
