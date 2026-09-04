from __future__ import annotations

from pathlib import Path

import pytest

from qlib_platform.model_runtime import (
    ModelProfile,
    ResolvedRuntime,
    StageTimings,
    build_model,
    load_model_profile,
    resolved_model_parameters,
    resolve_runtime,
)
from qlib_platform.models.registry import model_families
from qlib_platform.settings import Paths, Settings


def _settings(tmp_path: Path, research: dict[str, object] | None = None) -> Settings:
    config_path = tmp_path / "configs" / "pipeline.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("project_root: data\n", encoding="utf-8")
    return Settings(
        config_path=config_path,
        data={"research": research or {}},
        paths=Paths.from_root(tmp_path / "data"),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib",
    )


def test_model_profile_is_resolved_relative_to_pipeline_config(tmp_path):
    settings = _settings(tmp_path, {"model_profile": "model_profiles/cpu.yaml"})
    profile_path = settings.config_path.parent / "model_profiles" / "cpu.yaml"
    profile_path.parent.mkdir()
    profile_path.write_text(
        "name: cpu\nfamily: lightgbm\ndevice: cpu\nmodel_kwargs:\n  max_bin: 63\n",
        encoding="utf-8",
    )

    profile = load_model_profile(settings)

    assert profile.name == "cpu"
    assert profile.device == "cpu"
    assert profile.model_kwargs == {"max_bin": 63}
    assert profile.source == str(profile_path)


def test_profile_rejects_unknown_keys(tmp_path):
    settings = _settings(tmp_path)
    profile_path = tmp_path / "bad.yaml"
    profile_path.write_text(
        "name: bad\nfamily: lightgbm\ndevice: cpu\nunexpected: true\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown keys"):
        load_model_profile(settings, profile_path)


@pytest.mark.parametrize(
    "name",
    [
        "lightgbm_auto",
        "lightgbm_cpu_m5",
        "lightgbm_cpu_fast",
        "lightgbm_ci_smoke",
        "lightgbm_cpu_quantized",
        "lightgbm_cuda_nvidia",
        "lightgbm_gpu_windows",
        "pytorch_mps_m5",
        "ridge_golden_v1",
        "xgboost_cpu_v1",
    ],
)
def test_bundled_model_profiles_are_valid(tmp_path, name):
    settings = _settings(tmp_path)
    path = Path(__file__).parents[1] / "configs" / "model_profiles" / f"{name}.yaml"

    assert load_model_profile(settings, path).name == name


def test_lightgbm_auto_falls_back_but_explicit_cuda_fails(monkeypatch):
    monkeypatch.setattr("qlib_platform.models.adapters.lightgbm.sys.platform", "linux")
    monkeypatch.setattr(
        "qlib_platform.models.adapters.lightgbm.probe_cuda",
        lambda device_index: (False, "CUDA build missing", "4.7.0"),
    )
    auto = ModelProfile("auto", "lightgbm", "auto", 0, {}, "test")
    explicit = ModelProfile("cuda", "lightgbm", "cuda", 0, {}, "test")

    resolved = resolve_runtime(auto)

    assert resolved.resolved_device == "cpu"
    assert resolved.fallback_reason == "CUDA build missing"
    with pytest.raises(RuntimeError, match="CUDA build missing"):
        resolve_runtime(explicit)


def test_lightgbm_windows_auto_uses_opencl_gpu(monkeypatch):
    monkeypatch.setattr("qlib_platform.models.adapters.lightgbm.sys.platform", "win32")
    monkeypatch.setattr(
        "qlib_platform.models.adapters.lightgbm.probe_opencl",
        lambda platform_id, device_index: (True, None, "4.7"),
    )
    monkeypatch.setattr(
        "qlib_platform.models.adapters.lightgbm.opencl_device_name",
        lambda platform_id, device_index: "NVIDIA GeForce RTX 5060",
    )
    profile = ModelProfile("auto", "lightgbm", "auto", 0, {}, "test", 2)

    runtime = resolve_runtime(profile)
    params = resolved_model_parameters(runtime, feature_count=8, seed=42, num_threads=4)

    assert runtime.resolved_device == "gpu:0"
    assert runtime.device_name == "NVIDIA GeForce RTX 5060"
    assert runtime.to_manifest()["deviceName"] == "NVIDIA GeForce RTX 5060"
    assert params["device_type"] == "gpu"
    assert params["gpu_platform_id"] == 2
    assert params["gpu_device_id"] == 0
    assert params["gpu_use_dp"] is False


def test_runtime_fingerprint_includes_resolved_device():
    profile = ModelProfile("auto", "lightgbm", "auto", 0, {}, "test")

    cpu = ResolvedRuntime(profile, "cpu", "fallback", {"lightgbm": "4"})
    cuda = ResolvedRuntime(profile, "cuda:0", None, {"lightgbm": "4"})

    assert cpu.fingerprint != cuda.fingerprint


def test_stage_timings_use_monotonic_injected_clock():
    values = iter([0.0, 10.0, 11.5, 20.0, 23.0, 30.0])
    timings = StageTimings(clock=lambda: next(values))

    with timings.measure("data_seconds"):
        pass
    with timings.measure("train_seconds"):
        pass

    payload = timings.to_dict()
    assert payload["phasesSeconds"] == {"data_seconds": 1.5, "train_seconds": 3.0}
    assert payload["totalSeconds"] == 4.5
    assert payload["wallSeconds"] == 30.0


def test_lightgbm_model_uses_profile_threads_and_resolved_cpu():
    profile = ModelProfile("cpu", "lightgbm", "cpu", 0, {"num_threads": 3}, "test")
    runtime = ResolvedRuntime(profile, "cpu", None, {"lightgbm": "4"})

    model = build_model(runtime, feature_count=175, seed=42, num_threads=8)

    assert model.params["device_type"] == "cpu"
    assert model.params["num_threads"] == 3
    assert model.params["max_bin"] == 63
    assert "gpu_device_id" not in model.params


def test_dnn_rejects_stale_configured_input_dimension_before_importing_torch():
    profile = ModelProfile("dnn", "pytorch_dnn", "mps", 0, {"pt_model_kwargs": {"input_dim": 158}}, "test")
    runtime = ResolvedRuntime(profile, "mps", None, {"torch": "test"})

    with pytest.raises(ValueError, match="does not match dataset feature count 175"):
        build_model(runtime, feature_count=175, seed=42, num_threads=8)


def test_model_registry_exposes_complete_initial_family_set():
    assert model_families() == ("lightgbm", "pytorch_dnn", "ridge", "xgboost")


def test_ridge_is_a_deterministic_cpu_baseline():
    profile = ModelProfile("ridge", "ridge", "auto", 0, {"alpha": 2.0}, "test")

    runtime = resolve_runtime(profile)
    parameters = resolved_model_parameters(runtime, feature_count=10, seed=42, num_threads=8)
    model = build_model(runtime, feature_count=10, seed=42, num_threads=8)

    assert runtime.resolved_device == "cpu"
    assert parameters["estimator"] == "ridge"
    assert parameters["alpha"] == 2.0
    assert model.estimator == "ridge"


def test_xgboost_profile_builds_through_registered_adapter():
    pytest.importorskip("xgboost")
    profile = ModelProfile("xgb", "xgboost", "cpu", 0, {"num_boost_round": 17}, "test")

    runtime = resolve_runtime(profile)
    parameters = resolved_model_parameters(runtime, feature_count=10, seed=42, num_threads=3)
    model = build_model(runtime, feature_count=10, seed=42, num_threads=3)

    assert runtime.resolved_device == "cpu"
    assert parameters["device"] == "cpu"
    assert parameters["nthread"] == 3
    assert model.num_boost_round == 17
