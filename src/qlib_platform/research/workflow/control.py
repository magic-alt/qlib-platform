from __future__ import annotations

import hashlib
import json
import os
import socket
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml


_VOLATILE_KEYS = {
    "createdAtUtc",
    "created_at",
    "updatedAtUtc",
    "updated_at",
    "dashboard",
    "dashboardError",
    "status",
    "failureCount",
    "observedWarnings",
    "runtime",
    "result",
    "predictionBacktest",
    "exitCode",
    "warnings",
    "attempt",
    "startedAtUtc",
    "finishedAtUtc",
    "error",
}

_PATH_KEYS = {"config", "output", "localPath"}


def canonicalize(value: Any) -> Any:
    """Return the stable scientific/business identity projection of a value."""
    if isinstance(value, Mapping):
        return {
            str(key): canonicalize(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if key not in _VOLATILE_KEYS and key not in _PATH_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [canonicalize(item) for item in value]
    if isinstance(value, Path):
        return value.name
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(canonicalize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def identity(value: Any, *, prefix: str = "") -> str:
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"{prefix}{digest[:20]}"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def model_profile_identity(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        return {"name": resolved.name, "sha256": "MISSING", "parameters": None}
    raw = resolved.read_text(encoding="utf-8")
    try:
        parameters = yaml.safe_load(raw)
    except yaml.YAMLError:
        parameters = {"unparsedSha256": hashlib.sha256(raw.encode("utf-8")).hexdigest()}
    return {
        "name": resolved.name,
        "sha256": file_sha256(resolved),
        "parameters": parameters,
    }


def build_research_spec(
    *,
    dataset_ref: str,
    dataset_anchor: Mapping[str, Any] | None,
    mode: str,
    template: str | None,
    stage: str,
    alpha_packs: Sequence[str],
    model_profiles: Sequence[tuple[str, Path]],
    train: Sequence[str] | None,
    valid: Sequence[str] | None,
    test: Sequence[str] | None,
    start: str | None,
    end: str | None,
    benchmark: str,
    topn: int | None,
    artifact_level: str,
    prediction_backtest: bool,
    verification: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the canonical ResearchSpec used to identify and resume a study."""
    spec = {
        "schemaVersion": "2.0",
        "dataset": {
            "reference": dataset_ref,
            "versionId": (dataset_anchor or {}).get("versionId"),
            "dataReleaseId": (dataset_anchor or {}).get("dataReleaseId"),
        },
        "research": {
            "mode": mode,
            "template": template,
            "stage": stage,
            "alphaPacks": list(alpha_packs),
            "models": [
                {"name": name, "profile": model_profile_identity(profile)} for name, profile in model_profiles
            ],
            "split": {
                "train": list(train) if train else None,
                "valid": list(valid) if valid else None,
                "test": list(test) if test else None,
                "start": start,
                "end": end,
            },
            "labelIsolation": {
                "policy": "delegate-to-versioned-research-template",
                "purgeEmbargoRequired": True,
            },
            "portfolio": {
                "benchmark": benchmark,
                "topn": topn,
                "predictionBacktest": prediction_backtest,
            },
            "artifactLevel": artifact_level,
        },
        "verification": dict(verification),
    }
    spec["researchId"] = identity(spec, prefix="research-")
    return spec


def expand_matrix(
    alpha_packs: Sequence[str],
    model_profiles: Sequence[tuple[str, Path]],
    *,
    research_id: str,
) -> list[dict[str, Any]]:
    """Expand a deterministic AlphaPack x model matrix and assign stable cell ids."""
    del research_id  # Cell identity is deliberately independent of unrelated matrix members.
    rows: list[dict[str, Any]] = []
    for alpha in alpha_packs:
        for model, profile in model_profiles:
            cell = {
                "alphaPack": alpha,
                "model": model,
                "profile": model_profile_identity(profile),
            }
            cell["cellId"] = identity({"matrixSchema": "1.0", **cell}, prefix="cell-")
            rows.append(cell)
    return rows


def artifact_hashes(paths: Iterable[Path]) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in paths:
        resolved = path.expanduser().resolve()
        result[str(resolved)] = file_sha256(resolved) if resolved.is_file() else "MISSING"
    return result


def hashes_valid(recorded: Mapping[str, str]) -> bool:
    if not recorded:
        return False
    for raw, expected in recorded.items():
        path = Path(raw)
        if not path.is_file() or file_sha256(path) != expected:
            return False
    return True


@dataclass(frozen=True)
class ResumeDecision:
    reuse: bool
    reason: str
    attempt: int


class RunState:
    """Atomic stage ledger. Completed output is reusable only when hashes still match."""

    def __init__(self, path: Path, research_id: str):
        self.path = path
        self.research_id = research_id
        self.payload: dict[str, Any] = {
            "schemaVersion": "1.1",
            "researchId": research_id,
            "priorResearchIds": [],
            "history": [],
            "stages": {},
        }
        if path.is_file():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            prior = loaded.get("researchId")
            if prior and prior != research_id:
                prior_ids = loaded.setdefault("priorResearchIds", [])
                if prior not in prior_ids:
                    prior_ids.append(prior)
                loaded["researchId"] = research_id
            loaded.setdefault("history", [])
            loaded.setdefault("stages", {})
            self.payload = loaded

    def decide(self, stage: str, input_hash: str) -> ResumeDecision:
        record = self.payload.get("stages", {}).get(stage)
        if not isinstance(record, Mapping):
            return ResumeDecision(False, "no prior stage", 1)
        attempt = int(record.get("attempt", 0)) + 1
        if record.get("inputHash") != input_hash:
            return ResumeDecision(False, "scientific inputs changed", attempt)
        if record.get("status") != "SUCCEEDED":
            return ResumeDecision(False, "prior attempt incomplete", attempt)
        outputs = record.get("outputHashes", {})
        if not isinstance(outputs, Mapping) or not hashes_valid({str(k): str(v) for k, v in outputs.items()}):
            return ResumeDecision(False, "output missing or hash mismatch", attempt)
        return ResumeDecision(True, "verified prior output", int(record.get("attempt", 1)))

    def start(self, stage: str, input_hash: str) -> int:
        old = self.payload.setdefault("stages", {}).get(stage, {})
        if isinstance(old, Mapping) and old:
            self.payload.setdefault("history", []).append({"stage": stage, **dict(old)})
        attempt = int(old.get("attempt", 0)) + 1 if isinstance(old, Mapping) else 1
        self.payload["stages"][stage] = {
            "status": "RUNNING",
            "attempt": attempt,
            "inputHash": input_hash,
            "startedAtUtc": _utc_now(),
            "outputHashes": {},
        }
        self.write()
        return attempt

    def finish(
        self,
        stage: str,
        *,
        status: str,
        output_paths: Iterable[Path] = (),
        metadata: Mapping[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        record = self.payload.setdefault("stages", {}).setdefault(stage, {})
        record.update(
            status=status,
            finishedAtUtc=_utc_now(),
            outputHashes=artifact_hashes(output_paths),
        )
        if metadata:
            record["metadata"] = dict(metadata)
        if error:
            record["error"] = error
        self.write()

    def write(self) -> None:
        atomic_write_json(self.path, self.payload)


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


@contextmanager
def research_lock(path: Path, *, stale_after_seconds: int = 12 * 60 * 60):
    """Cross-platform fail-closed lease using exclusive file creation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    if path.exists() and now - path.stat().st_mtime > stale_after_seconds:
        path.unlink()
    payload = json.dumps(
        {"pid": os.getpid(), "host": socket.gethostname(), "acquiredAtUtc": _utc_now()},
        sort_keys=True,
    )
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise RuntimeError(f"research run is already leased: {path}") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        yield
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def governance_restrictions(current_state_text: str) -> dict[str, bool]:
    lowered = current_state_text.lower()
    return {
        "formalCandidatesAllowed": "formalcandidatesallowed=false" not in lowered
        and "formal candidates | disallowed" not in lowered,
        "modelSelectionAllowed": "model selection | disallowed" not in lowered,
        "finalHoldoutAccessAllowed": "finalholdout.accessallowed=false" not in lowered
        and "final holdout | `sealed`; access disallowed" not in lowered,
        "publishingAuthorized": "publishingauthorized=false" not in lowered
        and "publishing in phase 3-d | disabled" not in lowered,
    }


def enforce_governance(*, stage: str, mode: str, current_state_text: str) -> dict[str, bool]:
    policy = governance_restrictions(current_state_text)
    release_like = stage == "release" or mode == "walk-forward"
    if release_like and (
        not policy["formalCandidatesAllowed"]
        or not policy["modelSelectionAllowed"]
        or not policy["finalHoldoutAccessAllowed"]
        or not policy["publishingAuthorized"]
    ):
        raise PermissionError(
            "research plan rejected by active governance: release/walk-forward cannot bypass "
            "Phase 3-D candidate/model-selection/publishing restrictions or the sealed final holdout"
        )
    return policy
