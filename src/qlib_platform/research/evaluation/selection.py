from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from qlib_platform.lineage import sha256_json
from qlib_platform.data.store import sha256_file
from qlib_platform.research.contracts.candidate_program import assert_workstream_allowed, load_candidate_lock


def _load_json(path: str | Path, name: str) -> tuple[Path, dict[str, Any]]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"{name} is missing: {source}")
    value = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    return source, value


def _verified_lock(path: str | Path) -> tuple[Path, dict[str, Any]]:
    source = Path(path).expanduser().resolve()
    return source, load_candidate_lock(source)


def write_candidate_selection_lock(
    *,
    contract_lock: str | Path,
    candidates: Sequence[Mapping[str, object]],
    design_release_manifest: str | Path,
    selection_date: str,
    output: str | Path,
) -> Path:
    contract_path, lock = _verified_lock(contract_lock)
    assert_workstream_allowed(lock, "INCREMENTAL_ACCEPTANCE")
    if not 1 <= len(candidates) <= 3:
        raise ValueError("Phase 2 final holdout requires one to three selected candidates")
    normalized: list[dict[str, object]] = []
    candidate_ids: set[str] = set()
    required = {"candidateId", "alphaPack", "featureSet", "model", "portfolio", "regimeRule"}
    for raw in candidates:
        if missing := required - set(raw):
            raise ValueError(f"selected candidate is missing fields: {sorted(missing)}")
        if raw.get("status") != "RESEARCH_CANDIDATE" or raw.get("gatePass") is not True:
            raise ValueError("only gate-passing research candidates may enter the final holdout")
        candidate_id = str(raw["candidateId"])
        if candidate_id in candidate_ids:
            raise ValueError(f"duplicate selected candidate: {candidate_id}")
        candidate_ids.add(candidate_id)
        normalized.append({str(key): value for key, value in sorted(raw.items())})
    release_path, release = _load_json(design_release_manifest, "design DataRelease manifest")
    expected_profile = str(lock.get("contract", {}).get("data_release_profile") or "")
    if release.get("profile") != expected_profile:
        raise ValueError("design DataRelease does not use the Phase 2 profile")
    date = str(pd.Timestamp(selection_date).normalize().date())
    payload: dict[str, Any] = {
        "schemaVersion": "candidate_selection_lock_v1",
        "programId": lock.get("programId"),
        "contractLock": {
            "path": str(contract_path),
            "sha256": sha256_file(contract_path),
            "lockSha256": lock["lockSha256"],
        },
        "designDataRelease": {
            "path": str(release_path),
            "dataReleaseId": release.get("dataReleaseId"),
            "manifestSha256": release.get("manifestSha256"),
            "profile": release.get("profile"),
            "coverage": release.get("coverage"),
            "policies": release.get("policies"),
        },
        "selectionDate": date,
        "selectedCandidates": sorted(normalized, key=lambda item: str(item["candidateId"])),
        "finalHoldout": {
            "policy": "FIRST_SESSION_AFTER_SELECTION_LOCK",
            "sessions": int(lock["contract"]["holdout"]["sessions"]),
            "labelMaturitySessions": int(lock["contract"]["holdout"]["label_maturity_sessions"]),
            "accessLimit": 1,
            "status": "SEALED",
        },
        "publishingAuthorized": False,
    }
    payload["selectionLockSha256"] = sha256_json(payload)
    target = Path(output).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    os.replace(temporary, target)
    return target


def open_final_holdout(
    *,
    selection_lock: str | Path,
    final_release_manifest: str | Path,
    trading_calendar: Sequence[str | pd.Timestamp],
    output: str | Path,
) -> Path:
    lock_path, lock = _load_json(selection_lock, "Phase 2 selection lock")
    recorded = str(lock.get("selectionLockSha256") or "")
    actual = sha256_json({key: value for key, value in lock.items() if key != "selectionLockSha256"})
    if recorded != actual:
        raise ValueError("Phase 2 selection lock checksum mismatch")
    target = Path(output).expanduser().resolve()
    if target.exists():
        raise PermissionError("Phase 2 final holdout has already been opened for this selection lock")
    _, final_release = _load_json(final_release_manifest, "final-holdout DataRelease manifest")
    design = lock["designDataRelease"]
    if final_release.get("profile") != design.get("profile"):
        raise ValueError("final-holdout DataRelease profile drift")
    if final_release.get("policies") != design.get("policies"):
        raise ValueError("final-holdout DataRelease PIT policy drift")
    lineage = final_release.get("lineage")
    if not isinstance(lineage, Mapping) or lineage.get("parentDataReleaseId") != design.get("dataReleaseId"):
        raise ValueError("final-holdout DataRelease is not an append-only declared successor")
    if final_release.get("coverage", {}).get("start") != design.get("coverage", {}).get("start"):
        raise ValueError("final-holdout DataRelease changed historical coverage start")
    calendar = pd.DatetimeIndex(pd.to_datetime(list(trading_calendar), errors="raise")).normalize()
    calendar = calendar.drop_duplicates().sort_values()
    future = calendar[calendar > pd.Timestamp(lock["selectionDate"])]
    holdout = lock["finalHoldout"]
    sessions = int(holdout["sessions"])
    maturity = int(holdout["labelMaturitySessions"])
    if len(future) < sessions + maturity:
        raise ValueError("final holdout and label maturity window are not complete")
    start = future[0]
    end = future[sessions - 1]
    labels_mature = future[sessions + maturity - 1]
    if pd.Timestamp(final_release.get("coverage", {}).get("end")) < labels_mature:
        raise ValueError("final-holdout DataRelease coverage ends before labels mature")
    receipt: dict[str, Any] = {
        "schemaVersion": "final_holdout_open_receipt_v1",
        "selectionLock": {
            "path": str(lock_path),
            "sha256": sha256_file(lock_path),
            "selectionLockSha256": recorded,
        },
        "dataReleaseId": final_release.get("dataReleaseId"),
        "manifestSha256": final_release.get("manifestSha256"),
        "window": {
            "start": str(start.date()),
            "end": str(end.date()),
            "labelsMature": str(labels_mature.date()),
            "sessions": sessions,
        },
        "selectedCandidateIds": [item["candidateId"] for item in lock["selectedCandidates"]],
        "accessOrdinal": 1,
        "publishingAuthorized": False,
    }
    receipt["receiptSha256"] = sha256_json(receipt)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    os.replace(temporary, target)
    return target
