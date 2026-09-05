from __future__ import annotations

import importlib.util

import pytest

from qlib_platform.research.evidence.experiment_store import ExperimentStore
from qlib_platform.research.reporting.research_console import render_research_console


pytestmark = pytest.mark.skipif(importlib.util.find_spec("duckdb") is None, reason="duckdb not installed")


def test_duckdb_store_registers_and_compares_research_entities(tmp_path) -> None:
    with ExperimentStore(tmp_path / "research.duckdb") as store:
        store.register_model("model_a", family="lightgbm", config={"num_leaves": 31})
        store.register_factor(
            "factor_a",
            name="value",
            definition_sha256="a" * 64,
            metadata={"family": "valuation"},
        )
        store.register_experiment(
            "exp_a",
            dataset_id="dataset_1",
            feature_set_id="alpha158",
            model_id="model_a",
            git_sha="deadbeef",
            params={"seed": 7},
            lineage={"data_release_id": "release_1"},
        )
        store.register_experiment("exp_b", dataset_id="dataset_1", model_id="model_a")
        store.log_metrics("exp_a", {"ic": 0.05, "icir": 0.7})
        store.log_metrics("exp_b", {"ic": 0.03, "icir": 0.4})
        store.register_portfolio(
            "portfolio_a",
            experiment_id="exp_a",
            asof_date="2026-01-05",
            policy="mean_variance_v1",
            metrics={"turnover": 0.2},
        )
        store.register_artifact("exp_a", kind="prediction", uri="artifact://pred", sha256="b" * 64)

        comparison = store.compare_experiments(["exp_a", "exp_b"])
        assert comparison.loc["exp_a", "icir"] == 0.7
        detail = store.get_experiment("exp_a")
        assert detail is not None
        assert detail["params"] == {"seed": 7}
        assert len(detail["artifacts"]) == 1
        assert len(store.list_models()) == 1
        assert len(store.list_factors()) == 1
        assert len(store.list_portfolios()) == 1
        assert store.compare_models(["model_a"]).iloc[0]["family"] == "lightgbm"
        assert store.compare_factors(["factor_a"]).iloc[0]["name"] == "value"
        assert store.compare_portfolios(["portfolio_a"]).iloc[0]["metric:turnover"] == 0.2


def test_console_renders_all_research_catalogs(tmp_path) -> None:
    with ExperimentStore(tmp_path / "research.duckdb") as store:
        store.register_experiment("exp_a")
        store.log_metrics("exp_a", {"rank_ic": 0.06})
        body = render_research_console(store, compare_ids=["exp_a"])
    assert "Research Console" in body
    assert "Experiments" in body
    assert "Experiment comparison" in body
    assert "rank_ic" in body
