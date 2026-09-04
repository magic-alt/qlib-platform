from __future__ import annotations

import pytest

from qlib_platform.alpha import get_alpha_pack
from qlib_platform.canonical_config import CanonicalConfig
from qlib_platform.models.model_runtime import ModelProfile, ResolvedRuntime
from qlib_platform.research.research_experiment import ResearchExperimentSpec
from qlib_platform.research.research_timing import LabelSpec
from qlib_platform.settings import Paths, Settings


def _settings(tmp_path, experiment: dict[str, object]) -> Settings:
    return Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={
            "qlib": {"dataset_version": "ds_test"},
            "experiment": {"data_release": "ds_test", **experiment},
        },
        paths=Paths.from_root(tmp_path / "data"),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib",
    )


def _resolve(tmp_path, experiment: dict[str, object]) -> ResearchExperimentSpec:
    settings = _settings(tmp_path, experiment)
    profile = ModelProfile("one", "lightgbm", "cpu", 0, {}, "test")
    runtime = ResolvedRuntime(profile, "cpu", None, {})
    canonical = CanonicalConfig.from_settings(settings, runtime)
    return ResearchExperimentSpec.resolve(
        settings=settings,
        runtime=runtime,
        canonical=canonical,
        alpha_pack=get_alpha_pack("alpha158_pit_v1"),
        label_spec=LabelSpec(5, 1),
        train=("2020-01-01", "2022-12-31"),
        valid=("2023-01-01", "2023-06-30"),
        test=("2023-07-01", "2023-12-31"),
        run_kind="fixed_split",
        benchmark="SH000300",
    )


def test_experiment_identity_changes_with_model_but_not_alpha_identity(tmp_path):
    settings = _settings(tmp_path, {})
    profile = ModelProfile("one", "lightgbm", "cpu", 0, {}, "test")
    runtime = ResolvedRuntime(profile, "cpu", None, {})
    canonical = CanonicalConfig.from_settings(settings, runtime)
    kwargs = dict(
        settings=settings,
        canonical=canonical,
        alpha_pack=get_alpha_pack("alpha158_pit_v1"),
        label_spec=LabelSpec(5, 1),
        train=("2020-01-01", "2022-12-31"),
        valid=("2023-01-01", "2023-06-30"),
        test=("2023-07-01", "2023-12-31"),
        run_kind="fixed_split",
        benchmark="SH000300",
    )
    first = ResearchExperimentSpec.resolve(runtime=runtime, **kwargs)
    other_profile = ModelProfile("two", "lightgbm", "cpu", 0, {}, "test")
    other_runtime = ResolvedRuntime(other_profile, "cpu", None, {})
    other_canonical = CanonicalConfig.from_settings(settings, other_runtime)
    second = ResearchExperimentSpec.resolve(runtime=other_runtime, **{**kwargs, "canonical": other_canonical})

    assert first.alpha_pack_sha256 == second.alpha_pack_sha256
    assert first.experiment_id != second.experiment_id
    assert first.experiment_id == ResearchExperimentSpec.resolve(runtime=runtime, **kwargs).experiment_id


def test_experiment_accepts_local_dataset_without_configured_data_release(tmp_path):
    spec = _resolve(tmp_path, {"data_release": None})

    assert spec.data_release_id == "ds_test"


def test_experiment_rejects_explicit_data_release_mismatch(tmp_path):
    with pytest.raises(ValueError, match="data_release does not match"):
        _resolve(tmp_path, {"data_release": "different_release"})


def test_experiment_accepts_rank_buffer_v1_policy(tmp_path):
    spec = _resolve(tmp_path, {"portfolio": {"policy": "rank_buffer_v1"}})

    assert spec.portfolio_policy_id == "rank_buffer_v1"
    assert spec.portfolio_policy_sha256


def test_experiment_rejects_unknown_portfolio_policy(tmp_path):
    with pytest.raises(ValueError, match="unknown portfolio policy"):
        _resolve(tmp_path, {"portfolio": {"policy": "not_a_policy"}})
