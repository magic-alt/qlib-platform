from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from qlib_platform.lineage import sha256_json
from qlib_platform.research.workflow.timing import LabelSpec
from qlib_platform.data.store import sha256_file


REALIZED_LABEL_SCHEMA = "realized_label_snapshot_v1"
REALIZED_LABEL_TYPE = "REALIZED_LABEL_SNAPSHOT"
_CONTRACT_FIELDS = (
    "data_release_id",
    "label_spec_id",
    "horizon_days",
    "signal_lag_days",
    "price_field",
    "source_artifact_id",
)


@dataclass(frozen=True)
class RealizedLabelSpec:
    data_release_id: str
    label_spec_id: str
    horizon_days: int
    signal_lag_days: int
    price_field: str
    source_artifact_id: str

    def validate(self) -> None:
        values = asdict(self)
        missing = sorted(key for key, value in values.items() if not str(value).strip())
        if missing:
            raise ValueError(f"realized label contract has empty fields: {missing}")
        if self.horizon_days < 1 or self.signal_lag_days < 1:
            raise ValueError("realized label horizon and signal lag must be at least 1")
        if self.price_field not in {"open", "close"}:
            raise ValueError("realized label price field must be open or close")
        expected = LabelSpec(
            horizon_days=self.horizon_days,
            signal_lag_days=self.signal_lag_days,
            price_field=self.price_field,
        )
        if self.label_spec_id != expected.spec_id:
            raise ValueError(
                f"realized label spec identity mismatch: {self.label_spec_id} != {expected.spec_id}"
            )

    @property
    def lookahead_days(self) -> int:
        return self.horizon_days + self.signal_lag_days


def realized_label_manifest_path(payload_path: str | Path) -> Path:
    return Path(payload_path).with_suffix(".realized-labels.json")


def _normalize_calendar(calendar: Sequence[object] | pd.DatetimeIndex) -> pd.DatetimeIndex:
    dates = pd.DatetimeIndex(pd.to_datetime(list(calendar), errors="raise")).normalize()
    dates = dates.drop_duplicates().sort_values()
    if dates.empty:
        raise ValueError("realized label snapshot requires a non-empty trading calendar")
    return dates


def _normalize_labels(labels: pd.Series | pd.DataFrame) -> pd.DataFrame:
    frame = labels.to_frame("label") if isinstance(labels, pd.Series) else labels.copy()
    if not isinstance(frame.index, pd.MultiIndex) or frame.index.names != ["datetime", "instrument"]:
        raise ValueError("realized labels require a datetime/instrument MultiIndex")
    if "label" not in frame:
        if len(frame.columns) != 1:
            raise ValueError("realized labels require one label column")
        frame = frame.rename(columns={frame.columns[0]: "label"})
    result = frame[["label"]].copy()
    dates = pd.to_datetime(result.index.get_level_values("datetime"), errors="raise").normalize()
    instruments = result.index.get_level_values("instrument").astype(str)
    result.index = pd.MultiIndex.from_arrays([dates, instruments], names=["datetime", "instrument"])
    result = result.sort_index()
    result["label"] = pd.to_numeric(result["label"], errors="raise")
    if result.empty or not np.isfinite(result["label"].to_numpy(dtype=float)).all():
        raise ValueError("realized labels must be non-empty and finite")
    if result.index.has_duplicates:
        raise ValueError("realized labels contain duplicate datetime/instrument rows")
    if (result.index.get_level_values("instrument").astype(str).str.strip() == "").any():
        raise ValueError("realized labels contain an empty instrument")
    return result


def _identity(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": manifest["schemaVersion"],
        "artifactType": manifest["artifactType"],
        "contract": manifest["contract"],
        "observation": manifest["observation"],
        "payloadSha256": manifest["payload"]["sha256"],
        "rows": manifest["payload"]["rows"],
        "coverage": manifest["payload"]["coverage"],
    }


def write_realized_label_snapshot(
    payload_path: str | Path,
    labels: pd.Series | pd.DataFrame,
    *,
    spec: RealizedLabelSpec,
    trading_calendar: Sequence[object] | pd.DatetimeIndex,
    observed_through: str | pd.Timestamp,
) -> dict[str, Any]:
    """Write labels only after every signal date is mature on the pinned calendar."""

    spec.validate()
    frame = _normalize_labels(labels)
    calendar = _normalize_calendar(trading_calendar)
    observed = pd.Timestamp(observed_through).normalize()
    positions = pd.Series(range(len(calendar)), index=calendar)
    signal_dates = pd.DatetimeIndex(frame.index.get_level_values("datetime")).normalize().unique()
    missing_dates = signal_dates.difference(calendar)
    if len(missing_dates):
        raise ValueError(f"realized label dates are absent from trading calendar: {missing_dates.tolist()}")
    if observed not in positions.index:
        raise ValueError("observed_through must be an open date in the pinned trading calendar")
    immature = [
        str(date.date())
        for date in signal_dates
        if int(positions.loc[date]) + spec.lookahead_days > int(positions.loc[observed])
    ]
    if immature:
        raise ValueError(f"realized labels are not mature through {observed.date()}: {immature}")

    target = Path(payload_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        frame.to_parquet(temporary)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    dates = pd.DatetimeIndex(frame.index.get_level_values("datetime")).normalize()
    manifest: dict[str, Any] = {
        "schemaVersion": REALIZED_LABEL_SCHEMA,
        "artifactType": REALIZED_LABEL_TYPE,
        "contract": asdict(spec),
        "observation": {
            "observedThrough": str(observed.date()),
            "calendarSha256": sha256_json([str(value.date()) for value in calendar]),
            "calendarStart": str(calendar.min().date()),
            "calendarEnd": str(calendar.max().date()),
        },
        "payload": {
            "path": target.name,
            "sha256": sha256_file(target),
            "rows": len(frame),
            "columns": ["label"],
            "coverage": {
                "startDate": str(dates.min().date()),
                "endDate": str(dates.max().date()),
            },
            "instrumentCount": int(frame.index.get_level_values("instrument").nunique()),
        },
    }
    manifest["snapshotId"] = "rls_" + sha256_json(_identity(manifest))
    sidecar = realized_label_manifest_path(target)
    temporary_manifest = sidecar.with_name(f".{sidecar.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary_manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
        )
        os.replace(temporary_manifest, sidecar)
    finally:
        if temporary_manifest.exists():
            temporary_manifest.unlink()
    return manifest


def load_realized_label_snapshot(path: str | Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    source = Path(path).expanduser().resolve()
    sidecar = source if source.suffix == ".json" else realized_label_manifest_path(source)
    if not sidecar.is_file():
        raise FileNotFoundError(f"realized label manifest not found: {sidecar}")
    manifest = json.loads(sidecar.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("realized label manifest must be an object")
    if manifest.get("schemaVersion") != REALIZED_LABEL_SCHEMA:
        raise ValueError(f"unsupported realized label schema: {manifest.get('schemaVersion')}")
    if manifest.get("artifactType") != REALIZED_LABEL_TYPE:
        raise ValueError("realized label artifact type is invalid")
    if manifest.get("snapshotId") != "rls_" + sha256_json(_identity(manifest)):
        raise ValueError("realized label snapshot identity mismatch")
    contract = manifest.get("contract")
    if not isinstance(contract, Mapping):
        raise ValueError("realized label contract is missing")
    if set(contract) != set(_CONTRACT_FIELDS):
        raise ValueError("realized label contract fields are invalid")
    RealizedLabelSpec(
        data_release_id=str(contract["data_release_id"]),
        label_spec_id=str(contract["label_spec_id"]),
        horizon_days=int(contract["horizon_days"]),
        signal_lag_days=int(contract["signal_lag_days"]),
        price_field=str(contract["price_field"]),
        source_artifact_id=str(contract["source_artifact_id"]),
    ).validate()
    observation = manifest.get("observation")
    required_observation = {
        "observedThrough",
        "calendarSha256",
        "calendarStart",
        "calendarEnd",
    }
    if not isinstance(observation, Mapping) or set(observation) != required_observation:
        raise ValueError("realized label observation contract is invalid")
    if any(not str(observation[key]).strip() for key in required_observation):
        raise ValueError("realized label observation contract is incomplete")
    payload = manifest.get("payload")
    if not isinstance(payload, Mapping):
        raise ValueError("realized label payload metadata is missing")
    payload_path = (sidecar.parent / str(payload.get("path") or "")).resolve()
    if payload_path.parent != sidecar.parent or not payload_path.is_file():
        raise ValueError("realized label payload path is invalid")
    if sha256_file(payload_path) != payload.get("sha256"):
        raise ValueError("realized label payload checksum mismatch")
    frame = _normalize_labels(pd.read_parquet(payload_path))
    if len(frame) != int(payload.get("rows", -1)) or payload.get("columns") != ["label"]:
        raise ValueError("realized label payload schema or row count mismatch")
    dates = pd.DatetimeIndex(frame.index.get_level_values("datetime")).normalize()
    actual_coverage = {"startDate": str(dates.min().date()), "endDate": str(dates.max().date())}
    if payload.get("coverage") != actual_coverage:
        raise ValueError("realized label coverage mismatch")
    return frame, manifest
