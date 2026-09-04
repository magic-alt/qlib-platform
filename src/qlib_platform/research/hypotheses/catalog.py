from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from qlib_platform.lineage import sha256_json
from qlib_platform.data.store import sha256_file
from qlib_platform.research.contracts.candidate_program import load_candidate_lock
from qlib_platform.research.features.candidate_sets import HYPOTHESIS_FEATURE_SETS, HypothesisFeatureSetSpec


@dataclass(frozen=True)
class HypothesisRunBinding:
    hypothesis_id: str
    role: str
    feature_set_id: str
    hypothesis_definition_sha256: str
    contract_lock_sha256: str
    contract_lock_id: str

    @property
    def fingerprint(self) -> str:
        return sha256_json(asdict(self))

    def to_manifest(self) -> dict[str, str]:
        return {
            "hypothesisId": self.hypothesis_id,
            "role": self.role,
            "featureSetId": self.feature_set_id,
            "hypothesisDefinitionSha256": self.hypothesis_definition_sha256,
            "contractLockSha256": self.contract_lock_sha256,
            "contractLockId": self.contract_lock_id,
            "hypothesisBindingSha256": self.fingerprint,
        }


def hypothesis_definition_sha256(hypothesis: Mapping[str, Any]) -> str:
    return sha256_json(dict(hypothesis))


def hypothesis_feature_set(hypothesis_id: str, role: str) -> HypothesisFeatureSetSpec:
    normalized_id = str(hypothesis_id).strip().upper()
    normalized_role = str(role).strip().lower()
    if normalized_role not in {"candidate", "baseline"}:
        raise ValueError("hypothesis role must be candidate or baseline")
    feature_set_id = f"{normalized_id}_{normalized_role.upper()}"
    try:
        return HYPOTHESIS_FEATURE_SETS[feature_set_id]
    except KeyError as exc:
        raise ValueError(f"unsupported Phase 2 hypothesis binding: {normalized_id}") from exc


def bind_candidate_hypothesis(
    contract_lock: str | Path,
    hypothesis_id: str,
    role: str,
) -> HypothesisRunBinding:
    lock_path = Path(contract_lock).expanduser().resolve()
    lock = load_candidate_lock(lock_path)
    normalized_id = str(hypothesis_id).strip().upper()
    hypotheses = {
        str(item["hypothesis_id"]): item
        for item in lock["contract"].get("hypotheses", ())
        if isinstance(item, Mapping)
    }
    try:
        hypothesis = hypotheses[normalized_id]
    except KeyError as exc:
        raise ValueError(f"hypothesis is absent from the frozen contract: {normalized_id}") from exc
    spec = hypothesis_feature_set(normalized_id, role)
    return HypothesisRunBinding(
        hypothesis_id=normalized_id,
        role=spec.role,
        feature_set_id=spec.feature_set_id,
        hypothesis_definition_sha256=hypothesis_definition_sha256(hypothesis),
        contract_lock_sha256=sha256_file(lock_path),
        contract_lock_id=str(lock["lockSha256"]),
    )
