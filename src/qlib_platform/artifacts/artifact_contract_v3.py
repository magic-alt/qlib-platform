from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = "3.0"
PREVIOUS_SCHEMA_VERSION = "2.0"
SUPPORTED_CONTRACT_VERSIONS = (SCHEMA_VERSION, PREVIOUS_SCHEMA_VERSION)
_DATA_RELEASE_ID = re.compile(r"^ds_[0-9a-f]{64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_ID = re.compile(r"^art_[0-9a-f]{64}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_INSTRUMENT_ID = re.compile(r"^[A-Z0-9][A-Z0-9._:-]{1,95}$")


class TargetInstructionType(str, Enum):
    TARGETS = "TARGETS"
    CASH_ONLY = "CASH_ONLY"
    NO_SIGNAL = "NO_SIGNAL"
    REVOKE = "REVOKE"


class TargetSemantics(str, Enum):
    FULL_SNAPSHOT = "FULL_SNAPSHOT"
    DELTA = "DELTA"


class OmittedInstrumentPolicy(str, Enum):
    ZERO_TARGET = "ZERO_TARGET"
    UNCHANGED = "UNCHANGED"


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _text(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    return text


def _sha(value: object, label: str) -> str:
    digest = _text(value, label)
    if not _SHA256.fullmatch(digest):
        raise ValueError(f"{label} must be 64 lowercase hex characters")
    return digest


def _number(value: object, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def _timestamp(value: object, label: str) -> datetime:
    text = _text(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return parsed


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _sequence(value: object, label: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{label} must be an array")
    return value


def negotiate_contract_version(
    producer_supported: Iterable[str],
    consumer_supported: Iterable[str],
) -> str:
    producer = tuple(str(value).strip() for value in producer_supported)
    consumer = tuple(str(value).strip() for value in consumer_supported)
    for owner, versions in (("producer", producer), ("consumer", consumer)):
        if not versions or any(not value for value in versions):
            raise ValueError(f"{owner} supported contract versions must be non-empty")
        if len(versions) != len(set(versions)):
            raise ValueError(f"{owner} supported contract versions contain duplicates")
        unknown = sorted(set(versions) - set(SUPPORTED_CONTRACT_VERSIONS))
        if unknown:
            raise ValueError(f"{owner} advertised unsupported contract versions: {unknown}")
    common = set(producer) & set(consumer)
    for version in SUPPORTED_CONTRACT_VERSIONS:
        if version in common:
            return version
    raise ValueError("producer and consumer have no mutually supported artifact contract version")


def _currency(value: object, label: str) -> str:
    currency = _text(value, label).upper()
    if not _CURRENCY.fullmatch(currency):
        raise ValueError(f"{label} must be a 3-letter currency code")
    return currency


def _instrument(value: object) -> str:
    instrument = _text(value, "target.instrument").upper()
    if not _INSTRUMENT_ID.fullmatch(instrument):
        raise ValueError("target.instrument is not a provider-neutral identity")
    return instrument


def canonicalize_target_instruction_v3(
    value: Mapping[str, Any],
    *,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    if value.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError(f"target instruction schemaVersion must be {SCHEMA_VERSION}")
    try:
        instruction_type = TargetInstructionType(_text(value.get("instructionType"), "instructionType"))
    except ValueError as exc:
        raise ValueError("unsupported instructionType") from exc

    base_currency = _currency(value.get("baseCurrency"), "baseCurrency")
    valuation_basis = _text(value.get("valuationBasis"), "valuationBasis")
    calendar = _mapping(value.get("calendar"), "calendar")
    policy = _mapping(value.get("policy"), "policy")
    calendar_id = _text(calendar.get("calendarId"), "calendar.calendarId")
    calendar_sha = _sha(calendar.get("versionSha256"), "calendar.versionSha256")
    policy_sha = _sha(policy.get("strategyPolicySha256"), "policy.strategyPolicySha256")

    timing = _mapping(value.get("timing"), "timing")
    as_of = _timestamp(timing.get("asOfTime"), "timing.asOfTime")
    signal = _timestamp(timing.get("signalTime"), "timing.signalTime")
    produced = _timestamp(timing.get("producedAt"), "timing.producedAt")
    valid_from = _timestamp(timing.get("validFrom"), "timing.validFrom")
    trade_not_before = _timestamp(timing.get("tradeNotBefore"), "timing.tradeNotBefore")
    valid_until = _timestamp(timing.get("validUntil"), "timing.validUntil")
    if not (_utc(as_of) <= _utc(signal) <= _utc(produced)):
        raise ValueError("requires asOfTime <= signalTime <= producedAt")
    if not (_utc(signal) <= _utc(valid_from) <= _utc(trade_not_before) < _utc(valid_until)):
        raise ValueError("requires signalTime <= validFrom <= tradeNotBefore < validUntil")
    if observed_at is not None:
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if _utc(produced) > _utc(observed_at):
            raise ValueError("producedAt is in the future")
        if _utc(valid_until) < _utc(observed_at):
            raise ValueError("target instruction is expired")

    risk = _mapping(value.get("risk"), "risk")
    max_gross = _number(risk.get("maxGrossExposure"), "risk.maxGrossExposure")
    max_net = _number(risk.get("maxAbsNetExposure"), "risk.maxAbsNetExposure")
    if max_gross <= 0 or max_net < 0 or max_net > max_gross:
        raise ValueError("risk exposure limits are invalid")
    fx = _mapping(value.get("fx"), "fx")
    max_fx_age = int(_number(fx.get("maxAgeSeconds"), "fx.maxAgeSeconds"))
    if max_fx_age <= 0:
        raise ValueError("fx.maxAgeSeconds must be positive")

    targets: list[dict[str, Any]] = []
    seen: set[str] = set()
    gross = 0.0
    net = 0.0
    for raw in _sequence(value.get("targets", []), "targets"):
        target = _mapping(raw, "target")
        instrument = _instrument(target.get("instrument"))
        if instrument in seen:
            raise ValueError(f"duplicate target instrument: {instrument}")
        seen.add(instrument)
        weight = _number(target.get("targetWeight"), f"target {instrument} targetWeight")
        if weight < -1 or weight > 1:
            raise ValueError(f"target {instrument} targetWeight must be between -1 and 1")
        currency = _currency(target.get("currency"), f"target {instrument} currency")
        item: dict[str, Any] = {
            "instrument": instrument,
            "targetWeight": weight,
            "currency": currency,
        }
        if target.get("score") is not None:
            item["score"] = _number(target.get("score"), f"target {instrument} score")
        fx_values = (target.get("fxRateToBase"), target.get("fxAvailableAt"), target.get("fxQuoteId"))
        if currency == base_currency:
            if any(value is not None for value in fx_values):
                raise ValueError(f"target {instrument} must not attach base-currency FX evidence")
        else:
            if any(value is None for value in fx_values):
                raise ValueError(f"target {instrument} requires explicit FX valuation evidence")
            rate = _number(target.get("fxRateToBase"), f"target {instrument} fxRateToBase")
            if rate <= 0:
                raise ValueError(f"target {instrument} fxRateToBase must be positive")
            fx_available = _timestamp(target.get("fxAvailableAt"), f"target {instrument} fxAvailableAt")
            if _utc(fx_available) > _utc(as_of):
                raise ValueError(f"target {instrument} FX evidence is future information")
            if (_utc(as_of) - _utc(fx_available)).total_seconds() > max_fx_age:
                raise ValueError(f"target {instrument} FX evidence is stale")
            item.update(
                fxRateToBase=rate,
                fxAvailableAt=fx_available.isoformat(),
                fxQuoteId=_text(target.get("fxQuoteId"), f"target {instrument} fxQuoteId"),
            )
        gross += abs(weight)
        net += weight
        targets.append(item)

    semantics_raw = value.get("targetSemantics")
    omitted_raw = value.get("omittedInstrumentPolicy")
    cash_raw = value.get("cashWeight")
    cash_delta_raw = value.get("cashWeightDelta")
    supersedes = str(value.get("supersedesArtifactId") or "").strip() or None
    if supersedes is not None and not _ARTIFACT_ID.fullmatch(supersedes):
        raise ValueError("supersedesArtifactId must be art_<64 lowercase hex>")

    semantics: TargetSemantics | None = None
    omitted: OmittedInstrumentPolicy | None = None
    cash_weight: float | None = None
    cash_delta: float | None = None
    if instruction_type is TargetInstructionType.TARGETS:
        if not targets:
            raise ValueError("TARGETS requires target rows")
        try:
            semantics = TargetSemantics(_text(semantics_raw, "targetSemantics"))
            omitted = OmittedInstrumentPolicy(_text(omitted_raw, "omittedInstrumentPolicy"))
        except ValueError as exc:
            raise ValueError("invalid target semantics") from exc
        if semantics is TargetSemantics.FULL_SNAPSHOT:
            if omitted is not OmittedInstrumentPolicy.ZERO_TARGET:
                raise ValueError("FULL_SNAPSHOT requires ZERO_TARGET omission semantics")
            cash_weight = _number(cash_raw, "cashWeight")
            if not 0 <= cash_weight <= 1 or cash_delta_raw is not None:
                raise ValueError("FULL_SNAPSHOT requires cashWeight in [0,1] and no cashWeightDelta")
            if gross > max_gross + 1e-12 or abs(net) > max_net + 1e-12:
                raise ValueError("full snapshot exceeds risk exposure limits")
        else:
            if omitted is not OmittedInstrumentPolicy.UNCHANGED:
                raise ValueError("DELTA requires UNCHANGED omission semantics")
            if cash_raw is not None:
                raise ValueError("DELTA must not define cashWeight")
            cash_delta = _number(cash_delta_raw, "cashWeightDelta")
            if not -1 <= cash_delta <= 1:
                raise ValueError("cashWeightDelta must be between -1 and 1")
    elif instruction_type is TargetInstructionType.CASH_ONLY:
        if targets:
            raise ValueError("CASH_ONLY must not contain target rows")
        semantics = TargetSemantics.FULL_SNAPSHOT
        omitted = OmittedInstrumentPolicy.ZERO_TARGET
        cash_weight = _number(cash_raw, "cashWeight")
        if abs(cash_weight - 1.0) > 1e-12 or cash_delta_raw is not None:
            raise ValueError("CASH_ONLY requires cashWeight=1 and no cashWeightDelta")
    elif instruction_type is TargetInstructionType.NO_SIGNAL:
        if targets or semantics_raw is not None or cash_raw is not None or cash_delta_raw is not None:
            raise ValueError("NO_SIGNAL must not contain targets, target semantics, or cash instructions")
        if supersedes is not None:
            raise ValueError("NO_SIGNAL must not supersede an earlier target")
        omitted = OmittedInstrumentPolicy.UNCHANGED
    else:
        if targets or semantics_raw is not None or cash_raw is not None or cash_delta_raw is not None:
            raise ValueError("REVOKE must not contain targets, target semantics, or cash instructions")
        if supersedes is None:
            raise ValueError("REVOKE requires supersedesArtifactId")
        omitted = OmittedInstrumentPolicy.UNCHANGED

    return {
        "schemaVersion": SCHEMA_VERSION,
        "instructionType": instruction_type.value,
        "targetSemantics": semantics.value if semantics else None,
        "omittedInstrumentPolicy": omitted.value if omitted else None,
        "baseCurrency": base_currency,
        "valuationBasis": valuation_basis,
        "calendar": {"calendarId": calendar_id, "versionSha256": calendar_sha},
        "policy": {"strategyPolicySha256": policy_sha},
        "timing": {
            "asOfTime": as_of.isoformat(),
            "signalTime": signal.isoformat(),
            "producedAt": produced.isoformat(),
            "validFrom": valid_from.isoformat(),
            "tradeNotBefore": trade_not_before.isoformat(),
            "validUntil": valid_until.isoformat(),
        },
        "risk": {"maxGrossExposure": max_gross, "maxAbsNetExposure": max_net},
        "fx": {"maxAgeSeconds": max_fx_age},
        "cashWeight": cash_weight,
        "cashWeightDelta": cash_delta,
        "supersedesArtifactId": supersedes,
        "targets": sorted(targets, key=lambda item: str(item["instrument"])),
        "exposure": {
            "gross": None if semantics is TargetSemantics.DELTA else gross,
            "net": None if semantics is TargetSemantics.DELTA else net,
            "grossDelta": gross if semantics is TargetSemantics.DELTA else None,
            "netDelta": net if semantics is TargetSemantics.DELTA else None,
        },
    }


def assert_target_instruction_executable_at(value: Mapping[str, Any], *, at: datetime) -> dict[str, Any]:
    canonical = canonicalize_target_instruction_v3(value, observed_at=at)
    instruction_type = TargetInstructionType(canonical["instructionType"])
    if instruction_type in {TargetInstructionType.NO_SIGNAL, TargetInstructionType.REVOKE}:
        raise ValueError(f"{instruction_type.value} is a control instruction, not an execution target")
    trade_not_before = _timestamp(canonical["timing"]["tradeNotBefore"], "tradeNotBefore")
    if _utc(at) < _utc(trade_not_before):
        raise ValueError("target instruction is not executable yet")
    return canonical


def build_artifact_identity_v3(
    *,
    artifact_type: str,
    promotion_status: str,
    data_release_id: str,
    universe_release_id: str,
    source_manifest_sha256: str,
    payload_sha256: str,
    parent_artifact_ids: Sequence[str],
    contract_identity: Mapping[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    if not _DATA_RELEASE_ID.fullmatch(data_release_id):
        raise ValueError("dataReleaseId must be ds_<64 lowercase hex>")
    universe = _text(universe_release_id, "universeReleaseId")
    source_sha = _sha(source_manifest_sha256, "sourceManifestSha256")
    payload_sha = _sha(payload_sha256, "payloadSha256")
    parents = [str(value) for value in parent_artifact_ids]
    if len(parents) != len(set(parents)) or any(not _ARTIFACT_ID.fullmatch(value) for value in parents):
        raise ValueError("parentArtifactIds must contain unique art_<64 lowercase hex> identities")
    identity = {
        "schemaVersion": SCHEMA_VERSION,
        "artifactType": _text(artifact_type, "artifactType"),
        "promotionStatus": _text(promotion_status, "promotionStatus"),
        "dataReleaseId": data_release_id,
        "universeReleaseId": universe,
        "sourceManifestSha256": source_sha,
        "payloadSha256": payload_sha,
        "parentArtifactIds": parents,
        "contractIdentity": dict(contract_identity) if contract_identity is not None else None,
    }
    return "art_" + sha256_json(identity), identity


def validate_portable_payload_ref(
    payload_ref: Mapping[str, Any],
    *,
    bundle_root: Path | None = None,
) -> str:
    if payload_ref.get("mediaType") != "application/json":
        raise ValueError("artifact payload mediaType must be application/json")
    _sha(payload_ref.get("sha256"), "payloadRef.sha256")
    relative = _text(payload_ref.get("relativePath"), "payloadRef.relativePath")
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or path.suffix.lower() != ".json":
        raise ValueError("payloadRef.relativePath must be a safe relative JSON path")
    if bundle_root is not None:
        candidate = bundle_root.joinpath(*path.parts)
        if candidate.is_symlink():
            raise ValueError("payloadRef.relativePath must not be a symbolic link")
        root = bundle_root.resolve()
        resolved = candidate.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError("payloadRef.relativePath escapes the bundle root") from exc
    return path.as_posix()
