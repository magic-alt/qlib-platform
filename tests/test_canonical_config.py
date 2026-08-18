from __future__ import annotations

from pathlib import Path

from tushare_qlib.canonical_config import DatasetSpec, PortfolioSpec, StrategySpec
from tushare_qlib.settings import Paths, Settings
from tushare_qlib.topk_dropout import RankBufferPolicy, TopkDropoutPolicy


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
        },
    )

    configured = StrategySpec.from_settings(settings)
    overridden = StrategySpec.from_settings(settings, topk_override=25)

    assert (configured.topk, configured.n_drop, configured.hold_thresh) == (40, 3, 10)
    assert (overridden.topk, overridden.n_drop, overridden.hold_thresh) == (25, 3, 10)
    assert isinstance(configured.to_policy(), TopkDropoutPolicy)


def test_strategy_spec_resolves_rank_buffer_policy(tmp_path: Path):
    settings = _settings(
        tmp_path,
        {
            "strategy": {
                "policy": "rank_buffer_v1",
                "rank_buffer": {
                    "target_size": 10,
                    "entry_rank": 10,
                    "exit_rank": 20,
                    "max_replacements": 3,
                    "hold_thresh": 1,
                    "risk_degree": 0.95,
                },
            },
        },
    )

    spec = StrategySpec.from_settings(settings)
    overridden = StrategySpec.from_settings(settings, topk_override=12, n_drop_override=2)

    assert spec.policy == "rank_buffer_v1"
    policy = spec.to_policy()
    assert isinstance(policy, RankBufferPolicy)
    assert (policy.target_size, policy.entry_rank, policy.exit_rank) == (10, 10, 20)
    assert (policy.max_replacements, policy.hold_thresh, policy.risk_degree) == (3, 1, 0.95)
    # The topk CLI override maps to the rank-buffer target size.
    assert overridden.to_policy().target_size == 12
    assert overridden.to_policy().max_replacements == 2


def test_strategy_spec_rejects_unknown_policy(tmp_path: Path):
    settings = _settings(tmp_path, {"strategy": {"policy": "not_a_policy"}})

    try:
        StrategySpec.from_settings(settings)
    except ValueError as exc:
        assert "unknown strategy policy" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("unknown strategy policy must be rejected")


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
        },
    )

    spec = PortfolioSpec.from_settings(settings)

    assert spec.top_n == 12
    assert spec.max_exposure == 0.75
    assert spec.weighting == "equal"
