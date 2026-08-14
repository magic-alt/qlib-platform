from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from ..lineage import sha256_json


@dataclass(frozen=True)
class AlphaPackSpec:
    pack_id: str
    version: int
    handler_class: str
    required_qlib_fields: tuple[str, ...]
    required_release_components: tuple[str, ...]
    warmup_trading_days: int
    processor_recipe: str
    feature_groups: tuple[str, ...]

    @property
    def fingerprint(self) -> str:
        return sha256_json(asdict(self))

    def to_manifest(self) -> dict[str, Any]:
        return {**asdict(self), "alpha_pack_sha256": self.fingerprint}
