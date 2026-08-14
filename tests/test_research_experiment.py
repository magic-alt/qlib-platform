from __future__ import annotations

from tushare_qlib.alpha import get_alpha_pack
from tushare_qlib.canonical_config import CanonicalConfig
from tushare_qlib.model_runtime import ModelProfile, ResolvedRuntime
from tushare_qlib.research_experiment import ResearchExperimentSpec
from tushare_qlib.research_timing import LabelSpec
from tushare_qlib.settings import Paths, Settings


def test_experiment_identity_changes_with_model_but_not_alpha_identity(tmp_path):
    settings = Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={"qlib": {"dataset_version": "ds_test"}, "experiment": {"data_release": "ds_test"}},
        paths=Paths.from_root(tmp_path / "data"),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib",
    )
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
