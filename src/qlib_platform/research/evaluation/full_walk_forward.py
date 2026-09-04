from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from qlib_platform.lineage import sha256_json
from qlib_platform.data.store import sha256_file


@dataclass(frozen=True)
class RunEvidence:
    root: Path
    manifest: dict[str, Any]
    evidence: dict[str, Any]

    @classmethod
    def load(cls, root: str | Path) -> "RunEvidence":
        path = Path(root).expanduser().resolve()
        manifest_path = path / "manifest.json"
        evidence_path = path / "walk_forward_evidence.json"
        if not manifest_path.is_file() or not evidence_path.is_file():
            raise FileNotFoundError(f"walk-forward evidence bundle is incomplete: {path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        if evidence.get("systemAcceptance") != "PASS" or evidence.get("walkForwardIntegrity") != "PASS":
            raise ValueError(f"walk-forward model evidence did not pass: {path}")
        if "latestTargets" in manifest:
            raise ValueError(f"walk-forward acceptance run published forbidden targets: {path}")
        if manifest.get("walkForwardEvidence") != evidence:
            raise ValueError(f"walk-forward manifest/evidence mismatch: {path}")
        lock_path = path / "research_selection_lock.json"
        if not lock_path.is_file():
            raise FileNotFoundError(f"walk-forward selection lock is missing: {path}")
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        if lock != evidence.get("researchSelectionLock"):
            raise ValueError(f"walk-forward selection lock/evidence mismatch: {path}")
        expected_lock = sha256_json({key: value for key, value in lock.items() if key != "lockSha256"})
        if lock.get("lockSha256") != expected_lock:
            raise ValueError(f"walk-forward selection lock identity mismatch: {path}")
        return cls(path, manifest, evidence)

    def artifact(self, name: str) -> Path:
        direct = self.root / name
        if direct.is_file():
            return direct
        for artifact in self.manifest.get("artifacts", []):
            if isinstance(artifact, Mapping) and artifact.get("name") == name:
                path = Path(str(artifact.get("localPath") or "")).resolve()
                if path.is_file():
                    return path
        raise FileNotFoundError(f"walk-forward artifact is missing: {name} in {self.root}")

    def artifact_sha256(self, name: str) -> str:
        return sha256_file(self.artifact(name))


def _assert_exact_replay(reference: RunEvidence, replay: RunEvidence, model: str) -> dict[str, str]:
    exact: dict[str, str] = {}
    for name in (
        "oos_predictions.parquet",
        "oos_labels.parquet",
        "portfolio_report.parquet",
        "holdings.parquet",
        "final_holdout_predictions.parquet",
        "final_holdout_labels.parquet",
        "final_holdout_portfolio_report.parquet",
        "final_holdout_holdings.parquet",
    ):
        expected = reference.artifact_sha256(name)
        actual = replay.artifact_sha256(name)
        if actual != expected:
            raise ValueError(f"{model} resumed {name} is not exact")
        exact[name] = actual
    return exact


def _stable_contract(run: RunEvidence) -> dict[str, object]:
    lock = run.evidence.get("researchSelectionLock")
    feature = run.evidence.get("featureSnapshot")
    oos = run.evidence.get("oosPrediction")
    if not isinstance(lock, Mapping) or not isinstance(feature, Mapping) or not isinstance(oos, Mapping):
        raise ValueError(f"walk-forward contract evidence is incomplete: {run.root}")
    return {
        "dataRelease": lock.get("dataRelease"),
        "alphaPack": lock.get("alphaPack"),
        "labelSpec": lock.get("labelSpec"),
        "splitSpec": lock.get("splitSpec"),
        "portfolioPolicy": lock.get("portfolioPolicy"),
        "gateThresholds": lock.get("gateThresholds"),
        "codeCommit": lock.get("codeCommit"),
        "codeDirty": lock.get("codeDirty"),
        "featureSnapshotId": feature.get("featureSnapshotId"),
        "oosStart": oos.get("startDate"),
        "oosEnd": oos.get("endDate"),
        "oosSessions": oos.get("predictionDates"),
    }


def build_full_walk_forward_acceptance(
    runs: Mapping[str, tuple[str | Path, str | Path]],
    *,
    corruption_rebuild: str | Path,
    output: str | Path,
) -> Path:
    required_models = {"ridge", "lightgbm", "xgboost"}
    if set(runs) != required_models:
        raise ValueError(f"full acceptance requires exactly these models: {sorted(required_models)}")
    loaded = {name: (RunEvidence.load(pair[0]), RunEvidence.load(pair[1])) for name, pair in runs.items()}
    contracts = {name: _stable_contract(pair[0]) for name, pair in loaded.items()}
    first_contract = contracts["ridge"]
    if not first_contract.get("codeCommit") or first_contract.get("codeDirty") is not False:
        raise ValueError("Full Walk-forward Acceptance requires one clean committed code revision")
    drift = {name: value for name, value in contracts.items() if value != first_contract}
    if drift:
        raise ValueError(f"cross-model walk-forward contract drift: {sorted(drift)}")
    all_runs = [run for pair in loaded.values() for run in pair]
    if any(run.evidence.get("featureSnapshot", {}).get("rawMaterializationCalls") != 0 for run in all_runs):
        raise ValueError("Full Walk-forward Acceptance forbids raw feature materialization")

    label_hashes = {run.artifact_sha256("oos_labels.parquet") for run, _ in loaded.values()}
    if len(label_hashes) != 1:
        raise ValueError("cross-model rolling OOS labels are not exact")
    final_label_hashes = {run.artifact_sha256("final_holdout_labels.parquet") for run, _ in loaded.values()}
    if len(final_label_hashes) != 1:
        raise ValueError("cross-model final holdout labels are not exact")
    prediction_hashes = {
        name: reference.artifact_sha256("oos_predictions.parquet") for name, (reference, _) in loaded.items()
    }
    if len(set(prediction_hashes.values())) != len(prediction_hashes):
        raise ValueError("model predictions must differ across Ridge, LightGBM, and XGBoost")
    final_prediction_hashes = {
        name: reference.artifact_sha256("final_holdout_predictions.parquet")
        for name, (reference, _) in loaded.items()
    }
    if len(set(final_prediction_hashes.values())) != len(final_prediction_hashes):
        raise ValueError("final holdout model predictions must differ across accepted models")

    model_results: dict[str, object] = {}
    for name, (reference, resumed) in loaded.items():
        reuse_count = int(resumed.evidence.get("checkpointRecovery", {}).get("validFoldReuseCount", 0))
        if reuse_count < 1:
            raise ValueError(f"{name} resume evidence did not reuse any validated fold")
        model_results[name] = {
            "profile": reference.evidence.get("model", {}).get("profile"),
            "family": reference.evidence.get("model", {}).get("family"),
            "researchQuality": reference.evidence.get("researchQuality"),
            "predictionSha256": prediction_hashes[name],
            "finalHoldoutPredictionSha256": final_prediction_hashes[name],
            "resumedExact": _assert_exact_replay(reference, resumed, name),
            "checkpointReuseCount": reuse_count,
            "performance": reference.evidence.get("performance"),
            "researchStability": reference.evidence.get("researchStability"),
        }

    corruption = RunEvidence.load(corruption_rebuild)
    corruption_profile = str(corruption.evidence.get("model", {}).get("family") or "")
    matching = [
        name
        for name, (reference, _) in loaded.items()
        if reference.evidence.get("model", {}).get("family") == corruption_profile
    ]
    if len(matching) != 1:
        raise ValueError("corruption-rebuild evidence does not match exactly one accepted model")
    corruption_model = matching[0]
    invalidated = int(corruption.evidence.get("checkpointRecovery", {}).get("invalidatedAndRebuiltCount", 0))
    if invalidated < 1:
        raise ValueError("corrupted checkpoint payload was not invalidated and rebuilt")
    corruption_exact = _assert_exact_replay(loaded[corruption_model][0], corruption, corruption_model)

    state = [run.evidence.get("stateContinuity", {}) for run, _ in loaded.values()]
    if any(
        item.get("boundaryHoldingResetCount") != 0
        or item.get("boundaryCashResetCount") != 0
        or item.get("portfolioInitialCashEventCount") != 1
        for item in state
    ):
        raise ValueError("portfolio state continuity evidence failed")
    if any(not run.evidence.get("finalHoldout", {}).get("isolated") for run, _ in loaded.values()):
        raise ValueError("final holdout isolation evidence failed")

    result = {
        "acceptanceType": "FULL_WALK_FORWARD_V1",
        "systemAcceptance": "PASS",
        "walkForwardAcceptance": "PASS",
        "performanceAcceptance": "BASELINE_RECORDED",
        "data": {"dataRelease": first_contract["dataRelease"]},
        "featureSnapshot": {
            "featureSnapshotId": first_contract["featureSnapshotId"],
            "sameAcrossModels": True,
            "rawMaterializationCalls": 0,
        },
        "foldIntegrity": loaded["ridge"][0].evidence["foldIntegrity"],
        "stateContinuity": {
            "holdingResetCount": 0,
            "cashResetCount": 0,
            "portfolioInitialCashEventCount": 1,
        },
        "checkpointRecovery": {
            "passed": True,
            "validFoldsReused": True,
            "corruptedFoldInvalidatedAndRebuilt": True,
            "corruptionModel": corruption_model,
            "corruptionRebuildExact": corruption_exact,
        },
        "finalHoldout": {
            "isolated": True,
            "usedForResearchSelection": False,
            "accessedBeforeFinalization": False,
        },
        "determinism": {"ridge": "EXACT", "lightgbm": "EXACT", "xgboost": "EXACT"},
        "models": model_results,
    }
    target = Path(output).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    os.replace(temporary, target)
    return target
