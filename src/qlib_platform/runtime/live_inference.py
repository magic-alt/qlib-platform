from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd
import yaml

from qlib_platform.artifacts.artifact_resolver import ArtifactResolver, sha256_path
from qlib_platform.artifacts import ArtifactType
from qlib_platform.research.features.store import prepare_feature_data
from qlib_platform.datasets.dataset_resolver import pin_dataset
from qlib_platform.artifacts.live_artifacts import payload_sha256, stamp_live_artifact
from qlib_platform.models.model_bundle import LoadedModelBundle, load_model_bundle
from qlib_platform.models.model_registry import ModelRegistry
from qlib_platform.ops.ops_state import OpsState, SignalStatus
from qlib_platform.data.processors import AshareUniverseFilter, ProcessInfSingleThread
from qlib_platform.runtime.runtime_safety import resolve_qlib_parallel_runtime
from qlib_platform.settings import Settings
from qlib_platform.runtime.signal_health import SignalHealthReport, evaluate_signal_health
from qlib_platform.research.workflow.train_select import _next_trade_date


@dataclass(frozen=True)
class LiveInferenceResult:
    signal_id: str
    signal_date: str
    trade_date: str
    deployment_id: str
    score_path: Path
    topk_path: Path
    manifest_path: Path
    health: SignalHealthReport
    created: bool


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(
            json.dumps(dict(value), ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _production_resolver(settings: Settings) -> ArtifactResolver:
    return ArtifactResolver(settings.paths.output / "live")


def _approved_universe(bundle: LoadedModelBundle) -> Mapping[str, Any]:
    canonical = yaml.safe_load((bundle.root / "canonical_config.yaml").read_text(encoding="utf-8"))
    dataset = canonical.get("dataset", {}) if isinstance(canonical, Mapping) else {}
    filters = dataset.get("secondary_filters", {}) if isinstance(dataset, Mapping) else {}
    return filters if isinstance(filters, Mapping) else {}


def prepare_live_features(raw: pd.DataFrame, bundle: LoadedModelBundle) -> pd.DataFrame:
    universe = _approved_universe(bundle)
    filtered = AshareUniverseFilter(
        min_listed_days=int(universe.get("min_listed_days", 120)),
        min_circ_mv_yuan=float(universe.get("min_circ_mv_yuan", 2_000_000_000)),
        min_money_20d_yuan=float(universe.get("min_money_20d_yuan", 20_000_000)),
        exclude_st=bool(universe.get("exclude_st", True)),
        allow_unknown_st=bool(universe.get("allow_unknown_st", False)),
    )(raw.copy())
    filtered = ProcessInfSingleThread()(filtered)
    features = filtered["feature"].copy() if isinstance(filtered.columns, pd.MultiIndex) else filtered.copy()
    features.columns = features.columns.astype(str)
    missing = set(bundle.feature_columns) - set(features.columns)
    extra = set(features.columns) - set(bundle.feature_columns)
    if missing or extra:
        raise ValueError(
            f"live raw feature schema mismatch; missing={sorted(missing)}, extra={sorted(extra)}"
        )
    features = features.loc[:, bundle.feature_columns]
    values = features.to_numpy(dtype=float)
    values = (values - bundle.mean) / bundle.scale
    values = np.clip(values, -3.0, 3.0)
    values = np.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0)
    return pd.DataFrame(values, index=features.index, columns=bundle.feature_columns)


def _dataset_sha(settings: Settings) -> str:
    manifest = settings.qlib_data_uri / "dataset_manifest.json"
    if not manifest.is_file():
        raise FileNotFoundError(f"dataset manifest is missing: {manifest}")
    return sha256_path(manifest)


def _previous_live_score(
    settings: Settings, state: OpsState, *, signal_date: str, deployment_id: str
) -> pd.Series | None:
    previous = state.previous_pass_signal(signal_date=signal_date, deployment_id=deployment_id)
    if previous is None:
        return None
    manifest_path = _production_resolver(settings).resolve(str(previous["manifest_uri"]))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    score_path = manifest_path.parent / str(manifest["artifacts"]["score"]["path"])
    frame = pd.read_parquet(score_path)
    if not {"instrument", "score"}.issubset(frame.columns):
        raise ValueError("previous live score artifact is malformed")
    if frame["instrument"].duplicated().any():
        raise ValueError("previous live score contains duplicate instruments")
    return frame.set_index("instrument")["score"]


def run_live_inference(
    settings: Settings,
    *,
    as_of: str,
    deployment_id: str | None = None,
    require_daily_sync: bool = True,
    supersede: bool = False,
    health_now_utc: datetime | None = None,
) -> LiveInferenceResult:
    settings, _ = pin_dataset(settings)
    registry = ModelRegistry(settings)
    deployment = registry.state.deployment(deployment_id) if deployment_id else registry.current()
    if deployment["status"] != "DEPLOYED":
        raise ValueError("live inference requires a DEPLOYED model")
    deployment_id = str(deployment["deployment_id"])
    bundle = load_model_bundle(registry.bundle_root(deployment_id), verify_parity=True)
    canonical = yaml.safe_load((bundle.root / "canonical_config.yaml").read_text(encoding="utf-8"))
    import qlib
    from qlib.constant import REG_CN

    parallel = resolve_qlib_parallel_runtime(settings)
    qlib.init(
        provider_uri=str(settings.qlib_data_uri),
        region=REG_CN,
        expression_cache=None,
        dataset_cache=None,
        **parallel.qlib_init_kwargs(),
    )
    raw, feature_store = prepare_feature_data(settings, as_of, as_of)
    features = prepare_live_features(raw, bundle)
    score = bundle.predict(features).sort_index()
    score.index = score.index.set_names(["datetime", "instrument"])
    dates = pd.DatetimeIndex(score.index.get_level_values("datetime")).normalize().unique()
    expected = pd.Timestamp(as_of).normalize()
    if len(dates) != 1 or dates[0] != expected:
        raise ValueError("live inference did not produce exactly the requested signal date")
    trade_date = _next_trade_date(settings, expected)
    core = (
        pd.DataFrame(
            {
                "signal_date": expected.strftime("%Y-%m-%d"),
                "trade_date": trade_date,
                "instrument": score.index.get_level_values("instrument").astype(str),
                "score": score.to_numpy(dtype=float),
            }
        )
        .sort_values(["score", "instrument"], ascending=[False, True])
        .reset_index(drop=True)
    )
    core["score_rank"] = np.arange(1, len(core) + 1, dtype=int)
    topk_count = int(canonical["strategy"]["topk"])
    topk_core = core.head(topk_count).copy()
    topk_core["is_model_topk"] = True
    dataset_sha256 = _dataset_sha(settings)
    signal_sha256 = payload_sha256(core)
    topk_sha256 = payload_sha256(topk_core)
    identity = f"{deployment_id}|{dataset_sha256}|{as_of}|{signal_sha256}".encode()
    signal_id = hashlib.sha256(identity).hexdigest()[:32]
    reference_score = _previous_live_score(
        settings,
        registry.state,
        signal_date=expected.strftime("%Y-%m-%d"),
        deployment_id=deployment_id,
    )
    health_score = core.set_index("instrument")["score"]
    health = evaluate_signal_health(
        settings,
        health_score,
        signal_date=expected.strftime("%Y-%m-%d"),
        trade_date=trade_date,
        deployment=deployment,
        bundle_manifest=bundle.manifest,
        reference_score=reference_score,
        features=features,
        require_daily_sync=require_daily_sync,
        now_utc=health_now_utc,
    )
    signal_root = settings.paths.output / "live" / signal_id
    signal_root.mkdir(parents=True, exist_ok=True)
    attestation = {
        "schemaVersion": "3.0",
        "signalId": signal_id,
        "signalDate": expected.strftime("%Y-%m-%d"),
        "tradeDate": trade_date,
        "deploymentId": deployment_id,
        "datasetSha256": dataset_sha256,
        "signalSha256": signal_sha256,
        "artifactPayloads": {
            ArtifactType.MODEL_SCORE.value: signal_sha256,
            ArtifactType.MODEL_TOPK.value: topk_sha256,
        },
        "healthDecision": health.decision,
        "canonicalConfig": canonical,
    }
    attestation_path = signal_root / "attestation.json"
    _atomic_json(attestation_path, attestation)
    attestation_sha = sha256_path(attestation_path)
    manifest_uri = ArtifactResolver.signal_uri(signal_id, "attestation.json")
    governed = stamp_live_artifact(
        core,
        ArtifactType.MODEL_SCORE,
        deployment_id=deployment_id,
        dataset_sha256=dataset_sha256,
        signal_id=signal_id,
        manifest_uri=manifest_uri,
        manifest_sha256=attestation_sha,
    )
    score_path = signal_root / "model_score.parquet"
    governed.to_parquet(score_path, index=False)
    topk = stamp_live_artifact(
        topk_core,
        ArtifactType.MODEL_TOPK,
        deployment_id=deployment_id,
        dataset_sha256=dataset_sha256,
        signal_id=signal_id,
        manifest_uri=manifest_uri,
        manifest_sha256=attestation_sha,
    )
    topk_path = signal_root / "model_topk.csv"
    topk.to_csv(topk_path, index=False)
    health_path = signal_root / "signal_health.json"
    _atomic_json(health_path, health.to_dict())
    manifest = {
        **attestation,
        "artifactType": ArtifactType.MODEL_SCORE.value,
        "producer": "LIVE_INFERENCE",
        "bundleUri": deployment["bundle_uri"],
        "featureStore": feature_store,
        "health": health.to_dict(),
        "artifacts": {
            "score": {"path": score_path.name, "sha256": sha256_path(score_path), "rows": len(governed)},
            "topk": {"path": topk_path.name, "sha256": sha256_path(topk_path), "rows": len(topk)},
            "health": {"path": health_path.name, "sha256": sha256_path(health_path)},
            "attestation": {"path": attestation_path.name, "sha256": attestation_sha},
        },
    }
    manifest_path = signal_root / "manifest.json"
    _atomic_json(manifest_path, manifest)
    status = SignalStatus.PASS if health.passed else SignalStatus.REJECTED
    created = registry.state.register_signal(
        {
            "signal_id": signal_id,
            "signal_date": expected.strftime("%Y-%m-%d"),
            "trade_date": trade_date,
            "deployment_id": deployment_id,
            "dataset_sha256": dataset_sha256,
            "signal_sha256": signal_sha256,
            "manifest_uri": ArtifactResolver.signal_uri(signal_id),
            "status": status.value,
        },
        supersede=supersede,
    )
    return LiveInferenceResult(
        signal_id=signal_id,
        signal_date=expected.strftime("%Y-%m-%d"),
        trade_date=trade_date,
        deployment_id=deployment_id,
        score_path=score_path,
        topk_path=topk_path,
        manifest_path=manifest_path,
        health=health,
        created=created,
    )
