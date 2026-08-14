from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from .lineage import sha256_json


def _array_state(value: np.ndarray) -> dict[str, object]:
    array = np.ascontiguousarray(value)
    return {
        "type": "ndarray",
        "dtype": str(array.dtype),
        "shape": list(array.shape),
        "sha256": hashlib.sha256(array.tobytes()).hexdigest(),
    }


def _pandas_state(value: pd.Series | pd.DataFrame | pd.Index) -> dict[str, object]:
    if isinstance(value, pd.Index):
        frame = value.to_frame(index=False)
        kind = type(value).__name__
    elif isinstance(value, pd.Series):
        frame = value.to_frame()
        kind = "Series"
    else:
        frame = value
        kind = "DataFrame"
    hashes = pd.util.hash_pandas_object(frame, index=True, categorize=False).to_numpy()
    return {
        "type": kind,
        "shape": list(frame.shape),
        "columns": [str(column) for column in frame.columns],
        "dtypes": [str(dtype) for dtype in frame.dtypes],
        "sha256": hashlib.sha256(hashes.tobytes()).hexdigest(),
    }


def _stable_state(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return _array_state(value)
    if isinstance(value, (pd.Series, pd.DataFrame, pd.Index)):
        return _pandas_state(value)
    if isinstance(value, Mapping):
        return {str(key): _stable_state(item) for key, item in sorted(value.items(), key=lambda x: str(x[0]))}
    if isinstance(value, (list, tuple)):
        return [_stable_state(item) for item in value]
    if isinstance(value, set):
        return sorted((_stable_state(item) for item in value), key=str)
    state = getattr(value, "__dict__", None)
    if isinstance(state, Mapping):
        return {
            str(key): _stable_state(item)
            for key, item in sorted(state.items())
            if not str(key).startswith("_") and not callable(item)
        }
    return {"type": type(value).__qualname__, "repr": repr(value)}


def processor_state_manifest(
    handler: object,
    train: Sequence[str],
) -> dict[str, Any]:
    """Fingerprint fitted processor state without serializing processor instances.

    The fit window is part of the identity so two folds can never silently claim the
    same fitted state, even when a stateless processor recipe is used.
    """

    processors: list[dict[str, object]] = []
    for group in ("shared_processors", "infer_processors", "learn_processors"):
        values = getattr(handler, group, ()) or ()
        for position, processor in enumerate(values):
            state = _stable_state(processor)
            identity = {
                "group": group,
                "position": position,
                "class": f"{type(processor).__module__}.{type(processor).__qualname__}",
                "state": state,
            }
            processors.append({**identity, "stateSha256": sha256_json(identity)})
    payload = {
        "schemaVersion": "processor_state_v1",
        "fitWindow": [str(train[0]), str(train[1])],
        "processorCount": len(processors),
        "processors": processors,
    }
    return {**payload, "processorStateSha256": sha256_json(payload)}
