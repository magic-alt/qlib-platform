from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from qlib_platform.lineage import sha256_json


@dataclass(frozen=True)
class FactorDefinition:
    name: str
    family: str
    direction: int
    role: str = "alpha"
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("factor name is required")
        if not self.family.strip():
            raise ValueError(f"factor {self.name} requires a family")
        if self.direction not in {-1, 1}:
            raise ValueError(
                f"factor {self.name} direction must be predeclared as +1 or -1; "
                "inferring the sign from validation data is not allowed"
            )
        if self.role not in {"alpha", "exposure", "support"}:
            raise ValueError(f"factor {self.name} has unsupported role {self.role!r}")

    @property
    def fingerprint(self) -> str:
        return sha256_json(asdict(self))


@dataclass(frozen=True)
class FactorRegistry:
    registry_id: str
    factors: tuple[FactorDefinition, ...]

    def __post_init__(self) -> None:
        if not self.registry_id.strip():
            raise ValueError("factor registry id is required")
        names = [factor.name for factor in self.factors]
        if not names:
            raise ValueError("factor registry cannot be empty")
        if len(names) != len(set(names)):
            raise ValueError("factor registry contains duplicate factor names")

    @property
    def semantic_sha256(self) -> str:
        return sha256_json(
            {
                "registryId": self.registry_id,
                "factors": [asdict(factor) for factor in self.factors],
            }
        )

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(factor.name for factor in self.factors)

    def get(self, name: str) -> FactorDefinition:
        for factor in self.factors:
            if factor.name == name:
                return factor
        raise KeyError(f"factor is not registered: {name}")

    def validate_columns(self, columns: Sequence[object]) -> None:
        available = {str(value) for value in columns}
        missing = sorted(set(self.names) - available)
        if missing:
            raise ValueError(f"registered factors are absent from the feature frame: {missing}")

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "FactorRegistry":
        registry_id = str(payload.get("registryId") or payload.get("registry_id") or "").strip()
        raw = payload.get("factors")
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            raise ValueError("factor registry factors must be a list")
        factors: list[FactorDefinition] = []
        for item in raw:
            if not isinstance(item, Mapping):
                raise ValueError("each factor definition must be a mapping")
            factors.append(
                FactorDefinition(
                    name=str(item.get("name") or "").strip(),
                    family=str(item.get("family") or "").strip(),
                    direction=int(item.get("direction", 0)),
                    role=str(item.get("role") or "alpha").strip().lower(),
                    description=str(item.get("description") or ""),
                )
            )
        return cls(registry_id=registry_id, factors=tuple(factors))

    @classmethod
    def from_yaml(cls, path: str | Path) -> "FactorRegistry":
        source = Path(path)
        payload = yaml.safe_load(source.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("factor registry YAML root must be a mapping")
        return cls.from_mapping(payload)
