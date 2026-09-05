from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import pandas as pd

from qlib_platform.lineage import git_revision, sha256_json
from qlib_platform.models.base import ModelAdapter


_HOLDOUT_NAMES = frozenset({"holdout", "final_holdout", "test_holdout"})


@dataclass(frozen=True)
class SearchParameter:
    name: str
    kind: str
    low: float | int | None = None
    high: float | int | None = None
    choices: tuple[object, ...] = ()
    step: float | int | None = None
    log: bool = False

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("search parameter name is required")
        if self.kind not in {"float", "int", "categorical"}:
            raise ValueError(f"unsupported search parameter kind: {self.kind}")
        if self.kind == "categorical":
            if not self.choices:
                raise ValueError(f"categorical parameter {self.name} requires choices")
            if self.low is not None or self.high is not None:
                raise ValueError(f"categorical parameter {self.name} cannot define low/high")
        else:
            if self.low is None or self.high is None:
                raise ValueError(f"{self.kind} parameter {self.name} requires low/high")
            if float(self.low) >= float(self.high):
                raise ValueError(f"search parameter {self.name} requires low < high")
            if self.log and self.step is not None:
                raise ValueError(f"log parameter {self.name} cannot define step")

    def suggest(self, trial: Any) -> object:
        if self.kind == "categorical":
            return trial.suggest_categorical(self.name, list(self.choices))
        if self.low is None or self.high is None:
            raise ValueError(f"{self.kind} parameter {self.name} requires low/high")
        if self.kind == "int":
            int_kwargs: dict[str, Any] = {"log": self.log}
            if self.step is not None:
                int_kwargs["step"] = int(self.step)
            return trial.suggest_int(self.name, int(self.low), int(self.high), **int_kwargs)
        float_kwargs: dict[str, Any] = {"log": self.log}
        if self.step is not None:
            float_kwargs["step"] = float(self.step)
        return trial.suggest_float(
            self.name,
            float(self.low),
            float(self.high),
            **float_kwargs,
        )

    @classmethod
    def from_mapping(cls, name: str, value: Mapping[str, Any]) -> "SearchParameter":
        choices = value.get("choices", ())
        if isinstance(choices, (str, bytes)) or not isinstance(choices, Sequence):
            choices = ()
        return cls(
            name=name,
            kind=str(value.get("type") or value.get("kind") or "").strip().lower(),
            low=value.get("low"),
            high=value.get("high"),
            choices=tuple(choices),
            step=value.get("step"),
            log=bool(value.get("log", False)),
        )


@dataclass(frozen=True)
class SearchSpace:
    parameters: tuple[SearchParameter, ...]

    def __post_init__(self) -> None:
        names = [item.name for item in self.parameters]
        if not names:
            raise ValueError("search space must contain at least one parameter")
        if len(names) != len(set(names)):
            raise ValueError("search space contains duplicate parameter names")

    @property
    def fingerprint(self) -> str:
        return sha256_json([asdict(item) for item in self.parameters])

    def suggest(self, trial: Any) -> dict[str, object]:
        return {item.name: item.suggest(trial) for item in self.parameters}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SearchSpace":
        raw = value.get("parameters", value)
        if not isinstance(raw, Mapping):
            raise ValueError("search space must be a mapping")
        parameters: list[SearchParameter] = []
        for name, parameter in raw.items():
            if not isinstance(parameter, Mapping):
                raise ValueError(f"search parameter {name!r} must be a mapping")
            parameters.append(SearchParameter.from_mapping(str(name), parameter))
        return cls(tuple(parameters))


@dataclass(frozen=True)
class StudySpec:
    name: str
    dataset_version_id: str
    feature_snapshot_id: str
    model_family: str
    model_profile_id: str
    model_profile_fingerprint: str
    base_parameters_sha256: str
    code_commit: str
    objective_metric: str = "rank_ic_mean"
    direction: str = "maximize"
    seed: int = 42
    n_trials: int = 50
    timeout_seconds: int | None = None
    selection_segments: tuple[str, ...] = ("train", "valid")
    code_dirty: bool | None = None

    def __post_init__(self) -> None:
        required = {
            "name": self.name,
            "dataset_version_id": self.dataset_version_id,
            "feature_snapshot_id": self.feature_snapshot_id,
            "model_family": self.model_family,
            "model_profile_id": self.model_profile_id,
            "model_profile_fingerprint": self.model_profile_fingerprint,
            "base_parameters_sha256": self.base_parameters_sha256,
            "code_commit": self.code_commit,
            "objective_metric": self.objective_metric,
        }
        missing = sorted(key for key, value in required.items() if not str(value).strip())
        if missing:
            raise ValueError(f"study identity is incomplete: {missing}")
        normalized_segments = tuple(value.strip().lower() for value in self.selection_segments)
        if normalized_segments != ("train", "valid"):
            raise ValueError(
                "HPO selection segments are fixed to ('train', 'valid'); test/final holdout "
                "cannot participate in hyperparameter selection"
            )
        if _HOLDOUT_NAMES.intersection(normalized_segments):
            raise ValueError("final holdout cannot participate in HPO")
        if self.direction not in {"maximize", "minimize"}:
            raise ValueError("study direction must be maximize or minimize")
        if self.n_trials < 1:
            raise ValueError("n_trials must be positive")
        if self.timeout_seconds is not None and self.timeout_seconds < 1:
            raise ValueError("timeout_seconds must be positive")

    def identity(self, search_space: SearchSpace) -> dict[str, object]:
        return {
            "schemaVersion": "hpo_study_v1",
            "name": self.name,
            "datasetVersionId": self.dataset_version_id,
            "featureSnapshotId": self.feature_snapshot_id,
            "modelFamily": self.model_family,
            "modelProfileId": self.model_profile_id,
            "modelProfileFingerprint": self.model_profile_fingerprint,
            "baseParametersSha256": self.base_parameters_sha256,
            "codeCommit": self.code_commit,
            "codeDirty": self.code_dirty,
            "objectiveMetric": self.objective_metric,
            "direction": self.direction,
            "seed": self.seed,
            "nTrials": self.n_trials,
            "timeoutSeconds": self.timeout_seconds,
            "selectionSegments": list(self.selection_segments),
            "holdoutAccessAllowed": False,
            "searchSpaceSha256": search_space.fingerprint,
        }

    def study_id(self, search_space: SearchSpace) -> str:
        return "study_" + sha256_json(self.identity(search_space))[:24]

    @classmethod
    def from_repository(
        cls,
        *,
        repository_root: str | Path,
        name: str,
        dataset_version_id: str,
        feature_snapshot_id: str,
        model_family: str,
        model_profile_id: str,
        model_profile_fingerprint: str,
        base_parameters_sha256: str,
        **kwargs: Any,
    ) -> "StudySpec":
        revision = git_revision(Path(repository_root))
        commit = str(revision.get("commit") or "").strip()
        if not commit:
            raise ValueError("Git revision is required for HPO study identity")
        return cls(
            name=name,
            dataset_version_id=dataset_version_id,
            feature_snapshot_id=feature_snapshot_id,
            model_family=model_family,
            model_profile_id=model_profile_id,
            model_profile_fingerprint=model_profile_fingerprint,
            base_parameters_sha256=base_parameters_sha256,
            code_commit=commit,
            code_dirty=bool(revision.get("dirty")),
            **kwargs,
        )


def load_search_space_config(path: str | Path) -> tuple[dict[str, object], SearchSpace]:
    import yaml

    source = Path(path)
    payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("HPO search-space YAML root must be a mapping")
    if payload.get("schemaVersion") != "hpo_search_space_v1":
        raise ValueError(f"unsupported HPO search-space schema: {payload.get('schemaVersion')}")
    metadata: dict[str, object] = {
        "name": str(payload.get("name") or "").strip(),
        "objectiveMetric": str(payload.get("objectiveMetric") or "rank_ic_mean").strip(),
        "direction": str(payload.get("direction") or "maximize").strip(),
    }
    if not metadata["name"]:
        raise ValueError("HPO search-space name is required")
    return metadata, SearchSpace.from_mapping(payload)


@dataclass(frozen=True)
class TrialContext:
    number: int
    params: dict[str, object]
    seed: int


@dataclass(frozen=True)
class StudyResult:
    study_id: str
    manifest_path: Path
    best_trial_number: int
    best_params: dict[str, object]
    best_metrics: dict[str, float]


Objective = Callable[[TrialContext], Mapping[str, float]]


def _finite_metrics(metrics: Mapping[str, float]) -> dict[str, float]:
    result: dict[str, float] = {}
    for key, value in metrics.items():
        number = float(value)
        if math.isfinite(number):
            result[str(key)] = number
    return result


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(handle)
    temp = Path(temporary)
    try:
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def run_optuna_study(
    *,
    spec: StudySpec,
    search_space: SearchSpace,
    objective: Objective,
    output_root: str | Path,
    storage: str | None = None,
    base_parameters: Mapping[str, Any] | None = None,
) -> StudyResult:
    """Run a deterministic Optuna study over validation evidence only.

    The callback receives parameters and a deterministic per-trial seed.  It is not
    given a test/holdout handle.  Built-in model tuning below additionally constructs
    a dataset view whose ``test`` alias is the validation segment solely because some
    upstream Qlib models hard-code ``predict(..., segment='test')``.
    """

    persisted_base_parameters = dict(base_parameters or {})
    if sha256_json(persisted_base_parameters) != spec.base_parameters_sha256:
        raise ValueError("base parameter fingerprint does not match StudySpec")

    try:
        import optuna
    except ImportError as exc:
        raise RuntimeError("HPO requires Optuna; install qlib-platform[hpo]") from exc

    study_id = spec.study_id(search_space)
    sampler = optuna.samplers.TPESampler(seed=spec.seed)
    study = optuna.create_study(
        study_name=study_id,
        direction=spec.direction,
        sampler=sampler,
        storage=storage,
        load_if_exists=False,
    )

    def optuna_objective(trial: Any) -> float:
        params = search_space.suggest(trial)
        context = TrialContext(
            number=int(trial.number),
            params=params,
            seed=spec.seed + int(trial.number),
        )
        metrics = _finite_metrics(objective(context))
        if spec.objective_metric not in metrics:
            raise ValueError(
                f"objective did not produce finite metric {spec.objective_metric!r}; "
                f"available={sorted(metrics)}"
            )
        trial.set_user_attr("metrics", metrics)
        trial.set_user_attr("selectionSegments", list(spec.selection_segments))
        trial.set_user_attr("holdoutAccessed", False)
        return metrics[spec.objective_metric]

    study.optimize(
        optuna_objective,
        n_trials=spec.n_trials,
        timeout=spec.timeout_seconds,
        gc_after_trial=True,
    )
    if not study.trials:
        raise RuntimeError("Optuna study produced no trials")
    complete_trials = [
        trial
        for trial in study.trials
        if str(trial.state.name) == "COMPLETE"
        and trial.value is not None
        and math.isfinite(float(trial.value))
    ]
    if not complete_trials:
        raise RuntimeError("Optuna study produced no completed finite trials")
    best = study.best_trial
    best_metrics = _finite_metrics(best.user_attrs.get("metrics", {}))
    trials = []
    for trial in study.trials:
        metrics = _finite_metrics(trial.user_attrs.get("metrics", {}))
        trials.append(
            {
                "number": int(trial.number),
                "state": str(trial.state.name),
                "value": float(trial.value)
                if trial.value is not None and math.isfinite(trial.value)
                else None,
                "params": dict(trial.params),
                "metrics": metrics,
                "trialSeed": spec.seed + int(trial.number),
                "selectionSegments": ["train", "valid"],
                "holdoutAccessed": False,
            }
        )
    identity = spec.identity(search_space)
    manifest = {
        **identity,
        "studyId": study_id,
        "engine": {"name": "optuna", "version": str(optuna.__version__), "sampler": "TPESampler"},
        "searchSpace": [asdict(item) for item in search_space.parameters],
        "baseParameters": persisted_base_parameters,
        "trialCount": len(trials),
        "trials": trials,
        "bestTrial": {
            "number": int(best.number),
            "params": dict(best.params),
            "metrics": best_metrics,
        },
        "governance": {
            "purpose": "validation-only hyperparameter research",
            "formalCandidateCreated": False,
            "modelPromotionAuthorized": False,
            "finalHoldoutAccessed": False,
        },
    }
    parent = Path(output_root)
    parent.mkdir(parents=True, exist_ok=True)
    root = parent / study_id
    manifest_path = root / "study_manifest.json"
    if root.exists():
        if not manifest_path.is_file():
            raise ValueError(f"incomplete immutable HPO study exists: {root}")
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing != manifest:
            raise ValueError(f"immutable HPO study already exists with different evidence: {manifest_path}")
    else:
        building = Path(tempfile.mkdtemp(prefix=".hpo-study-building-", dir=parent))
        try:
            _atomic_json(building / manifest_path.name, manifest)
            os.replace(building, root)
        finally:
            if building.exists():
                shutil.rmtree(building, ignore_errors=True)
    return StudyResult(
        study_id=study_id,
        manifest_path=manifest_path,
        best_trial_number=int(best.number),
        best_params=dict(best.params),
        best_metrics=best_metrics,
    )


def _restricted_validation_dataset(dataset: Any) -> Any:
    """Return a DatasetH that contains train+valid only.

    ``test`` intentionally aliases ``valid`` to satisfy Qlib models whose public
    prediction method is hard-coded to the test segment.  The original test or final
    holdout range is not copied into this view.
    """

    from qlib.data.dataset import DatasetH

    segments = dict(getattr(dataset, "segments", {}))
    if "train" not in segments or "valid" not in segments:
        raise ValueError("HPO dataset requires train and valid segments")
    return DatasetH(
        handler=dataset.handler,
        segments={
            "train": segments["train"],
            "valid": segments["valid"],
            "test": segments["valid"],
        },
    )


def _normalize_label(label: pd.Series | pd.DataFrame) -> pd.Series:
    if isinstance(label, pd.DataFrame):
        if "label" in label.columns:
            result = label["label"]
        elif label.shape[1] == 1:
            result = label.iloc[:, 0]
        else:
            raise ValueError("validation labels must contain exactly one label")
    else:
        result = label
    return pd.to_numeric(result, errors="coerce").rename("label")


def validation_signal_metrics(
    predictions: pd.Series | pd.DataFrame,
    labels: pd.Series | pd.DataFrame,
    *,
    min_cross_section: int = 20,
) -> dict[str, float]:
    if isinstance(predictions, pd.DataFrame):
        if "score" in predictions:
            score = predictions["score"]
        elif predictions.shape[1] == 1:
            score = predictions.iloc[:, 0]
        else:
            raise ValueError("validation predictions must contain one score column")
    else:
        score = predictions
    score = pd.to_numeric(score, errors="coerce").rename("score")
    label = _normalize_label(labels)
    aligned = pd.concat([score, label], axis=1, join="inner").dropna()
    if not isinstance(aligned.index, pd.MultiIndex) or "datetime" not in aligned.index.names:
        raise ValueError("validation predictions require a MultiIndex containing datetime")

    daily_ic: list[float] = []
    daily_rank_ic: list[float] = []
    for _, block in aligned.groupby(level="datetime", sort=True):
        if len(block) < min_cross_section:
            continue
        if block["score"].nunique() < 2 or block["label"].nunique() < 2:
            continue
        daily_ic.append(float(block["score"].corr(block["label"], method="pearson")))
        daily_rank_ic.append(float(block["score"].corr(block["label"], method="spearman")))

    def ratio(values: list[float]) -> float:
        clean = np.asarray([value for value in values if np.isfinite(value)], dtype=float)
        if len(clean) < 2:
            return float("nan")
        std = float(clean.std(ddof=1))
        return float(clean.mean() / std) if std > 0 else float("nan")

    return {
        "ic_mean": float(np.nanmean(daily_ic)) if daily_ic else float("nan"),
        "rank_ic_mean": float(np.nanmean(daily_rank_ic)) if daily_rank_ic else float("nan"),
        "icir": ratio(daily_ic),
        "rank_icir": ratio(daily_rank_ic),
        "valid_ic_days": float(len(daily_ic)),
        "valid_rank_ic_days": float(len(daily_rank_ic)),
    }


def model_validation_objective(
    *,
    adapter: ModelAdapter,
    dataset: Any,
    base_parameters: Mapping[str, Any],
    min_cross_section: int = 20,
) -> Objective:
    """Build a validation-only Optuna objective for any registered ModelAdapter."""

    restricted = _restricted_validation_dataset(dataset)

    def objective(context: TrialContext) -> Mapping[str, float]:
        parameters = {**dict(base_parameters), **context.params}
        # Keep each trial's stochastic state explicit in the artifact evidence.
        for seed_key in ("seed", "feature_fraction_seed", "bagging_seed", "data_random_seed"):
            if seed_key in parameters:
                parameters[seed_key] = context.seed
        model = adapter.build(parameters)
        model.fit(restricted)
        predictions = model.predict(restricted)
        from qlib.data.dataset.handler import DataHandlerLP

        labels = restricted.prepare("valid", col_set="label", data_key=DataHandlerLP.DK_L)
        return validation_signal_metrics(
            predictions,
            labels,
            min_cross_section=min_cross_section,
        )

    return objective
