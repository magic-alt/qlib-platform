from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

import pandas as pd

from qlib_platform.artifacts import ARTIFACT_SCHEMA_VERSION, ArtifactType, validate_artifact


def default_lean_symbol(instrument: str) -> tuple[str, str, str]:
    value = str(instrument).upper().strip()
    if len(value) != 8 or value[:2] not in {"SH", "SZ", "BJ"} or not value[2:].isdigit():
        raise ValueError(f"unsupported Qlib instrument: {instrument}")
    market = {"SH": "XSHG", "SZ": "XSHE", "BJ": "XBSE"}[value[:2]]
    ticker = value[2:]
    return ticker, market, f"{ticker}.{market}"


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def export_lean_targets(
    targets: pd.DataFrame,
    output_dir: str | Path,
    *,
    signal_date: str,
    trade_date: str,
    model_id: str,
    dataset_id: str,
    symbol_map: Mapping[str, str] | None = None,
) -> tuple[Path, Path]:
    metadata = validate_artifact(targets, ArtifactType.TARGET_PORTFOLIO)
    for name, supplied in (("model_id", model_id), ("dataset_id", dataset_id)):
        if supplied != metadata[name]:
            raise ValueError(f"{name} does not match the governed target artifact")
    required = {"instrument", "target_weight"}
    missing = required - set(targets.columns)
    if missing:
        raise ValueError(f"targets missing columns: {sorted(missing)}")
    frame = targets.copy()
    frame["instrument"] = frame["instrument"].astype(str).str.upper().str.strip()
    frame["target_weight"] = pd.to_numeric(frame["target_weight"], errors="raise")
    if frame["instrument"].duplicated().any():
        raise ValueError("duplicate instruments in targets")
    if (frame["target_weight"] < -1e-12).any() or float(frame["target_weight"].sum()) > 1.000001:
        raise ValueError("invalid target weights")

    rows: list[dict[str, object]] = []
    for row in frame.sort_values("instrument").itertuples(index=False):
        instrument = str(row.instrument)
        ticker, market, generated = default_lean_symbol(instrument)
        lean_symbol = symbol_map.get(instrument, generated) if symbol_map else generated
        rows.append(
            {
                "instrument": instrument,
                "ticker": ticker,
                "market": market,
                "lean_symbol": lean_symbol,
                "target_weight": round(float(row.target_weight), 12),
                "score": (
                    round(float(getattr(row, "score")), 12)
                    if hasattr(row, "score") and pd.notna(getattr(row, "score"))
                    else None
                ),
            }
        )
    checksum = hashlib.sha256(_canonical_json(rows)).hexdigest()
    envelope = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "artifact_type": ArtifactType.TARGET_PORTFOLIO.value,
        "promotion_status": metadata["promotion_status"],
        "run_id": metadata["run_id"],
        "lineage_id": metadata["lineage_id"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "signal_date": pd.Timestamp(signal_date).strftime("%Y-%m-%d"),
        "trade_date": pd.Timestamp(trade_date).strftime("%Y-%m-%d"),
        "model_id": model_id,
        "dataset_id": dataset_id,
        "currency": "CNY",
        "target_count": len(rows),
        "gross_target_exposure": round(sum(float(str(r["target_weight"])) for r in rows), 12),
        "targets_sha256": checksum,
        "targets": rows,
    }
    out = Path(output_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    key = pd.Timestamp(trade_date).strftime("%Y%m%d")
    json_path = out / f"lean_targets_{key}.json"
    csv_path = out / f"lean_targets_{key}.csv"
    tmp_json = json_path.with_suffix(".json.tmp")
    tmp_json.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_json, json_path)
    tmp_csv = csv_path.with_suffix(".csv.tmp")
    pd.DataFrame(rows).to_csv(tmp_csv, index=False, encoding="utf-8")
    os.replace(tmp_csv, csv_path)
    return json_path, csv_path
