from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from qlib_platform.lineage import sha256_json


def write_immutable_json(
    payload: dict[str, Any],
    output: str | Path,
    identity_key: str,
) -> Path:
    """Write a deterministic checksummed JSON artifact exactly once.

    The identity is computed before the identity field is inserted, matching
    the established research artifact contract. Re-running with identical
    content is idempotent; an existing divergent artifact fails closed.
    """

    payload[identity_key] = sha256_json(payload)
    target = Path(output).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
        if existing != payload:
            raise ValueError(f"existing {payload['schemaVersion']} artifact differs")
        return target

    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, target)
    return target
