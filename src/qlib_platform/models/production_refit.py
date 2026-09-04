from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np
from qlib_platform.alpha.registry import alpha_pack_from_settings, assert_alpha_pack_compatible

from qlib_platform.canonical_config import CanonicalConfig
from qlib_platform.research.features.store import feature_store_enabled, prepare_feature_data
from qlib_platform.datasets.dataset_resolver import pin_dataset
from qlib_platform.lineage import git_revision, resolve_qlib_repo, sha256_json
from qlib_platform.models.model_bundle import create_model_bundle
from qlib_platform.models.model_registry import ModelRegistry
from qlib_platform.models.model_runtime import (
    build_model,
    load_model_profile,
    resolve_runtime,
    resolved_model_parameters,
)
from qlib_platform.models.registry import get_model_adapter
from qlib_platform.research.workflow.timing import (
    effective_label_gap,
    label_spec_from_settings,
    label_timing_from_settings,
    shared_research_calendar,
)
from qlib_platform.runtime.runtime_safety import resolve_qlib_parallel_runtime
from qlib_platform.settings import Settings
from qlib_platform.data.store import sha256_file
from qlib_platform.research.workflow.train_select import _configure_mlflow_tracking, _dataset_id, build_dataset


@dataclass(frozen=True)
class ProductionRefitPlan:
    selection_train: tuple[str, str]
    selection_valid: tuple[str, str]
    final_train: tuple[str, str]
    label_safe_end: str


def _research_manifest(settings: Settings, value: str | Path) -> tuple[Path, dict[str, Any]]:
    path = Path(value).expanduser()
    if not path.is_absolute() and len(path.parts) == 1:
        path = settings.paths.output / "research" / str(value) / "manifest.json"
    elif path.is_dir():
        path = path / "manifest.json"
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"research release manifest is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    promotion = payload.get("promotion", {})
    if payload.get("runKind") != "walk_forward":
        raise ValueError("production refit requires a promoted walk-forward research release")
    if not isinstance(promotion, Mapping) or promotion.get("status") != "PROMOTED":
        raise ValueError("research release is not PROMOTED")
    if promotion.get("decision") != "PROMOTE":
        raise ValueError("research release decision is not PROMOTE")
    lineage = payload.get("lineage", {})
    if not isinstance(lineage, Mapping) or not lineage.get("complete"):
        raise ValueError("research release lineage is incomplete")
    return path, payload


def production_refit_plan(settings: Settings, as_of: str) -> ProductionRefitPlan:
    calendar = shared_research_calendar(settings)
    signal_date = np.datetime64(as_of)
    matches = np.flatnonzero(calendar.values.astype("datetime64[D]") == signal_date.astype("datetime64[D]"))
    if len(matches) != 1:
        raise ValueError(f"refit as-of date is not in the shared trading calendar: {as_of}")
    position = int(matches[0])
    research = settings.data.get("research", {})
    walk = research.get("walk_forward", {}) if isinstance(research, Mapping) else {}
    if not isinstance(walk, Mapping):
        walk = {}
    train_days = int(walk.get("train_days", 1500))
    valid_days = int(walk.get("valid_days", 126))
    timing = label_timing_from_settings(settings)
    _, purge_days = effective_label_gap(walk.get("purge_days"), timing)
    _, embargo_days = effective_label_gap(walk.get("embargo_days"), timing)
    valid_end_pos = position - embargo_days
    valid_start_pos = valid_end_pos - valid_days + 1
    train_end_pos = valid_start_pos - purge_days - 1
    train_start_pos = train_end_pos - train_days + 1
    if train_start_pos < 0:
        raise ValueError("insufficient history for the approved production refit recipe")

    def fmt(index: int) -> str:
        return str(calendar[index].strftime("%Y-%m-%d"))

    selection_train = (fmt(train_start_pos), fmt(train_end_pos))
    selection_valid = (fmt(valid_start_pos), fmt(valid_end_pos))
    final_train = (selection_train[0], fmt(valid_end_pos))
    return ProductionRefitPlan(
        selection_train=selection_train,
        selection_valid=selection_valid,
        final_train=final_train,
        label_safe_end=fmt(valid_end_pos),
    )


def production_refit_windows(settings: Settings, as_of: str) -> tuple[tuple[str, str], tuple[str, str]]:
    """Return the final production-fit window and its selection validation tail."""

    plan = production_refit_plan(settings, as_of)
    return plan.final_train, plan.selection_valid


def _selected_training_steps(model: Any, family: str, parameters: Mapping[str, Any]) -> int:
    return get_model_adapter(family).selected_training_steps(model, parameters)


def _fit_final_model(model: Any, dataset: Any, family: str, selected_steps: int) -> None:
    get_model_adapter(family).fit_final(model, dataset, selected_steps)


def _assert_recipe(settings: Settings, release: Mapping[str, Any], canonical: Mapping[str, Any]) -> None:
    approved = release.get("canonicalConfig")
    if not isinstance(approved, Mapping):
        raise ValueError("research release has no canonical model recipe")
    current = dict(canonical)
    approved_copy = dict(approved)
    current_model = current.get("model", {})
    approved_model = approved_copy.get("model", {})
    if not isinstance(current_model, Mapping) or not isinstance(approved_model, Mapping):
        raise ValueError("canonical model recipe is malformed")
    if current_model.get("profile_name") != approved_model.get("profile_name"):
        raise ValueError("configured model profile differs from approved research recipe")
    if current_model.get("family") != approved_model.get("family"):
        raise ValueError("configured model family differs from approved research recipe")
    runtime_keys = {"device_type", "gpu_device_id", "gpu_platform_id", "gpu_use_dp", "GPU"}
    current_parameters = current_model.get("parameters", {})
    approved_parameters = approved_model.get("parameters", {})
    if not isinstance(current_parameters, Mapping) or not isinstance(approved_parameters, Mapping):
        raise ValueError("canonical model parameters are malformed")
    current_recipe = {key: value for key, value in current_parameters.items() if key not in runtime_keys}
    approved_recipe = {key: value for key, value in approved_parameters.items() if key not in runtime_keys}
    if sha256_json(current_recipe) != sha256_json(approved_recipe):
        raise ValueError("configured model parameters differ from approved research recipe")
    for section in ("dataset", "strategy", "portfolio", "risk"):
        if sha256_json(current.get(section)) != sha256_json(approved_copy.get(section)):
            raise ValueError(f"configured {section} differs from approved research recipe")


def refit_production_model(settings: Settings, research_run: str | Path, *, as_of: str) -> Path:
    settings, _ = pin_dataset(settings)
    alpha_pack = alpha_pack_from_settings(settings)
    assert_alpha_pack_compatible(settings, alpha_pack)
    _, release = _research_manifest(settings, research_run)
    profile = load_model_profile(settings)
    runtime = resolve_runtime(profile)
    approved_runtime = release.get("runtime", {})
    if (
        not isinstance(approved_runtime, Mapping)
        or approved_runtime.get("profileFingerprint") != profile.fingerprint
    ):
        raise ValueError("configured model profile fingerprint differs from approved research release")
    plan = production_refit_plan(settings, as_of)
    research = settings.data.get("research", {})
    research = research if isinstance(research, Mapping) else {}
    seed = int(research.get("random_seed", 42))
    np.random.seed(seed)
    experiment = settings.data.get("experiment", {})
    experiment = experiment if isinstance(experiment, Mapping) else {}
    alpha_config = experiment.get("alpha", {})
    alpha_config = alpha_config if isinstance(alpha_config, Mapping) else {}
    feature_set_id = str(alpha_config.get("feature_set") or "").strip() or None
    selected_technical = tuple(str(value) for value in alpha_config.get("selected_technical", ()))

    import qlib
    from qlib.constant import REG_CN
    from qlib.workflow import R

    parallel = resolve_qlib_parallel_runtime(settings)
    qlib.init(
        provider_uri=str(settings.qlib_data_uri),
        region=REG_CN,
        expression_cache=None,
        dataset_cache=None,
        **parallel.qlib_init_kwargs(),
    )
    prepared = None
    feature_store_metadata = None
    if feature_store_enabled(settings):
        prepared, feature_store_metadata = prepare_feature_data(settings, plan.selection_train[0], as_of)
    selection_dataset = build_dataset(
        train=plan.selection_train,
        valid=plan.selection_valid,
        test=(as_of, as_of),
        universe=dict(settings.data.get("universe", {})),
        label_spec=label_spec_from_settings(settings),
        prepared_feature_data=prepared,
        alpha_pack=alpha_pack,
        feature_set_id=feature_set_id,
        selected_technical=selected_technical,
    )
    feature_columns = [str(column) for column in selection_dataset.handler.get_cols(col_set="feature")]
    parameters = resolved_model_parameters(
        runtime,
        feature_count=len(feature_columns),
        seed=seed,
        num_threads=int(research.get("num_threads", 8)),
    )
    canonical = CanonicalConfig.from_settings(settings, runtime, model_parameters=parameters).to_manifest()
    _assert_recipe(settings, release, canonical)
    selection_model = build_model(
        runtime,
        feature_count=len(feature_columns),
        seed=seed,
        num_threads=int(research.get("num_threads", 8)),
    )
    _configure_mlflow_tracking(settings)
    with R.start(experiment_name="production_refit", uri=os.environ["MLFLOW_TRACKING_URI"]):
        selection_model.fit(selection_dataset)
        selected_steps = _selected_training_steps(selection_model, runtime.profile.family, parameters)
        dataset = build_dataset(
            train=plan.final_train,
            valid=plan.selection_valid,
            test=(as_of, as_of),
            universe=dict(settings.data.get("universe", {})),
            label_spec=label_spec_from_settings(settings),
            prepared_feature_data=prepared,
            alpha_pack=alpha_pack,
            feature_set_id=feature_set_id,
            selected_technical=selected_technical,
        )
        final_columns = [str(column) for column in dataset.handler.get_cols(col_set="feature")]
        if final_columns != feature_columns:
            raise ValueError("production final-fit feature schema differs from selection fit")
        model = build_model(
            runtime,
            feature_count=len(final_columns),
            seed=seed,
            num_threads=int(research.get("num_threads", 8)),
        )
        _fit_final_model(model, dataset, runtime.profile.family, selected_steps)
    dataset_manifest = settings.qlib_data_uri / "dataset_manifest.json"
    if not dataset_manifest.is_file():
        raise FileNotFoundError(f"dataset manifest is required for production refit: {dataset_manifest}")
    release_lineage = release.get("lineage", {})
    lineage = {
        "researchLineageId": release_lineage.get("lineageId")
        if isinstance(release_lineage, Mapping)
        else None,
        "researchRunId": str(release["externalRunId"]),
        "qlibPlatform": git_revision(Path(__file__).resolve().parents[2]),
        "qlib": git_revision(resolve_qlib_repo(settings.qlib_repo)),
    }
    manifest_path = create_model_bundle(
        settings,
        model=model,
        dataset=dataset,
        family=runtime.profile.family,
        model_parameters=parameters,
        canonical_config=canonical,
        research_run_id=str(release["externalRunId"]),
        refit_as_of=as_of,
        train_window=plan.final_train,
        valid_window=plan.selection_valid,
        dataset_id=_dataset_id(settings),
        dataset_sha256=sha256_file(dataset_manifest),
        feature_store=feature_store_metadata,
        lineage=lineage,
        seed=seed,
        runtime=runtime.to_manifest(),
        refit_metadata={
            "policy": "select_then_refit_all_label_safe",
            "selectionTrainWindow": list(plan.selection_train),
            "selectionValidWindow": list(plan.selection_valid),
            "labelSafeEndDate": plan.label_safe_end,
            "selectedTrainingSteps": selected_steps,
        },
    )
    ModelRegistry(settings).register_bundle(manifest_path)
    return manifest_path
