from __future__ import annotations

from pathlib import Path

from tushare_qlib.canonical_config import DatasetSpec, PortfolioSpec, StrategySpec
from tushare_qlib.settings import Paths, Settings


def _settings(tmp_path: Path, data: dict[str, object]) -> Settings:
    return Settings(
        config_path=tmp_path / "pipeline.yaml",
        data=data,
        paths=Paths.from_root(tmp_path / "data"),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib",
    )


def test_strategy_spec_is_the_single_source_for_topk_parameters(tmp_path: Path):
    settings = _settings(
        tmp_path,
        {
            "strategy": {"topk_dropout": {"topk": 40, "n_drop": 3, "hold_thresh": 10}},
            "execution": {"topk_dropout": {"topk": 99, "n_drop": 99}},
        },
    )

    configured = StrategySpec.from_settings(settings)
    overridden = StrategySpec.from_settings(settings, topk_override=25)

    assert (configured.topk, configured.n_drop, configured.hold_thresh) == (40, 3, 10)
    assert (overridden.topk, overridden.n_drop, overridden.hold_thresh) == (25, 3, 10)


def test_dataset_spec_records_lean_pit_universe(tmp_path: Path):
    settings = _settings(
        tmp_path,
        {
            "qlib": {"dataset_version": "csi300-v1"},
            "data_source": {"kind": "lean_mysql", "mysql": {"universe": "CSI300"}},
            "universe": {"min_listed_days": 120},
        },
    )

    spec = DatasetSpec.from_settings(settings)

    assert spec.universe_name == "CSI300"
    assert spec.membership_type == "point_in_time"
    assert spec.secondary_filters == {"min_listed_days": 120}


def test_portfolio_spec_comes_only_from_pipeline_portfolio_section(tmp_path: Path):
    settings = _settings(
        tmp_path,
        {
            "portfolio": {"top_n": 12, "max_exposure": 0.75, "weighting": "equal"},
            "execution": {"portfolio": {"top_n": 99, "max_exposure": 1.0}},
        },
    )

    spec = PortfolioSpec.from_settings(settings)

    assert spec.top_n == 12
    assert spec.max_exposure == 0.75
    assert spec.weighting == "equal"
