from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from ..lineage import sha256_json
from ..store import sha256_file

TAXONOMY_SCHEMA = "alpha_factor_taxonomy_v1"
ALLOWED_FAMILIES = {
    "Momentum",
    "Value",
    "Quality",
    "Growth",
    "Liquidity",
    "Flow",
    "Volatility",
    "Reversal",
    "Size",
    "TechnicalOther",
    "StateSupport",
    "Profitability",
    "Investment",
    "Accruals",
    "LowRisk",
    "FundamentalMomentum",
    "Interaction",
}
ALLOWED_ROLES = {"alpha", "exposure", "support"}
ALLOWED_DIRECTIONS = {"positive", "negative", "unknown"}


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: yaml.SafeLoader, node: yaml.nodes.MappingNode, deep: bool = False) -> dict:
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError(f"duplicate taxonomy key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,
)


@dataclass(frozen=True)
class FactorTaxonomyEntry:
    feature: str
    family: str
    role: str
    direction: str

    @property
    def ranking_eligible(self) -> bool:
        return self.role == "alpha"

    @property
    def orientation(self) -> float | None:
        if self.direction == "positive":
            return 1.0
        if self.direction == "negative":
            return -1.0
        return None

    def to_dict(self) -> dict[str, object]:
        return {
            "family": self.family,
            "role": self.role,
            "direction": self.direction,
            "rankingEligible": self.ranking_eligible,
        }


@dataclass(frozen=True)
class FactorTaxonomy:
    taxonomy_id: str
    alpha_pack_id: str
    entries: Mapping[str, FactorTaxonomyEntry]
    semantic_sha256: str
    file_sha256: str

    def entry(self, feature: str) -> FactorTaxonomyEntry:
        try:
            return self.entries[feature]
        except KeyError as exc:
            raise ValueError(f"feature is absent from taxonomy: {feature}") from exc

    def to_manifest(self) -> dict[str, object]:
        return {
            "schemaVersion": TAXONOMY_SCHEMA,
            "taxonomyId": self.taxonomy_id,
            "alphaPackId": self.alpha_pack_id,
            "semanticSha256": self.semantic_sha256,
            "fileSha256": self.file_sha256,
            "featureCount": len(self.entries),
        }


def load_factor_taxonomy(
    path: str | Path,
    *,
    expected_features: list[str] | tuple[str, ...] | None = None,
    expected_alpha_pack_id: str | None = None,
) -> FactorTaxonomy:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"factor taxonomy is missing: {source}")
    raw = yaml.load(source.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    if not isinstance(raw, Mapping):
        raise ValueError("factor taxonomy must be a mapping")
    if raw.get("schema") != TAXONOMY_SCHEMA:
        raise ValueError(f"unsupported factor taxonomy schema: {raw.get('schema')}")
    taxonomy_id = str(raw.get("taxonomyId") or "").strip()
    alpha_pack_id = str(raw.get("alphaPackId") or "").strip()
    if not taxonomy_id or not alpha_pack_id:
        raise ValueError("factor taxonomy requires taxonomyId and alphaPackId")
    if expected_alpha_pack_id and alpha_pack_id != expected_alpha_pack_id:
        raise ValueError(f"factor taxonomy AlphaPack mismatch: {alpha_pack_id} != {expected_alpha_pack_id}")
    feature_mapping = raw.get("features")
    if not isinstance(feature_mapping, Mapping) or not feature_mapping:
        raise ValueError("factor taxonomy features must be a non-empty mapping")

    entries: dict[str, FactorTaxonomyEntry] = {}
    semantic_features: dict[str, dict[str, str]] = {}
    for raw_name, raw_entry in feature_mapping.items():
        name = str(raw_name).strip()
        if not name or not isinstance(raw_entry, Mapping):
            raise ValueError(f"invalid taxonomy entry: {raw_name}")
        family = str(raw_entry.get("family") or "").strip()
        role = str(raw_entry.get("role") or "").strip()
        direction = str(raw_entry.get("direction") or "").strip()
        if family not in ALLOWED_FAMILIES:
            raise ValueError(f"feature {name} has invalid family: {family}")
        if role not in ALLOWED_ROLES:
            raise ValueError(f"feature {name} has invalid role: {role}")
        if direction not in ALLOWED_DIRECTIONS:
            raise ValueError(f"feature {name} has invalid direction: {direction}")
        entry = FactorTaxonomyEntry(name, family, role, direction)
        entries[name] = entry
        semantic_features[name] = {
            "family": family,
            "role": role,
            "direction": direction,
        }

    if expected_features is not None:
        expected = list(expected_features)
        if len(expected) != len(set(expected)):
            raise ValueError("expected feature contract contains duplicate names")
        missing = sorted(set(expected) - set(entries))
        unexpected = sorted(set(entries) - set(expected))
        if missing or unexpected:
            raise ValueError(
                f"factor taxonomy does not exactly cover the feature contract: "
                f"missing={missing}, unexpected={unexpected}"
            )

    semantic: dict[str, Any] = {
        "schema": TAXONOMY_SCHEMA,
        "taxonomyId": taxonomy_id,
        "alphaPackId": alpha_pack_id,
        "features": semantic_features,
    }
    return FactorTaxonomy(
        taxonomy_id=taxonomy_id,
        alpha_pack_id=alpha_pack_id,
        entries=entries,
        semantic_sha256=sha256_json(semantic),
        file_sha256=sha256_file(source),
    )
