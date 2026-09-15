from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path

import pytest

from qlib_platform.artifacts.artifact_contract_v3 import (
    PREVIOUS_SCHEMA_VERSION,
    SCHEMA_VERSION,
    SUPPORTED_CONTRACT_VERSIONS,
    assert_target_instruction_executable_at,
    build_artifact_identity_v3,
    canonicalize_target_instruction_v3,
    negotiate_contract_version,
    sha256_json,
    validate_portable_payload_ref,
)
from qlib_platform.artifacts.institutional_artifacts import SCHEMA_VERSION as V2_SCHEMA_VERSION


FIXTURE = Path(__file__).parent / "fixtures" / "artifact_v3" / "contract_cases.json"


def _cases() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _instruction(name: str) -> dict:
    return copy.deepcopy(_cases()["instructions"][name])


def _observed(value: str = "2026-09-15T10:00:00+08:00") -> datetime:
    return datetime.fromisoformat(value)


def test_v3_is_additive_and_keeps_v2_as_the_approved_previous_version() -> None:
    assert SCHEMA_VERSION == "3.0"
    assert PREVIOUS_SCHEMA_VERSION == V2_SCHEMA_VERSION == "2.0"
    assert SUPPORTED_CONTRACT_VERSIONS == ("3.0", "2.0")
    assert (Path(__file__).parent / "fixtures" / "artifact_v2" / "golden_v2").is_dir()


def test_shared_fixture_locks_policy_and_calendar_content_hashes() -> None:
    fixture = _cases()
    full = fixture["instructions"]["fullSnapshot"]

    assert sha256_json(fixture["policy"]) == full["policy"]["strategyPolicySha256"]
    assert sha256_json(fixture["calendarContract"]) == full["calendar"]["versionSha256"]


def test_full_snapshot_has_zero_target_omission_cash_and_cross_currency_fx() -> None:
    canonical = canonicalize_target_instruction_v3(_instruction("fullSnapshot"))

    assert canonical["instructionType"] == "TARGETS"
    assert canonical["targetSemantics"] == "FULL_SNAPSHOT"
    assert canonical["omittedInstrumentPolicy"] == "ZERO_TARGET"
    assert canonical["cashWeight"] == pytest.approx(0.1)
    assert canonical["cashWeightDelta"] is None
    assert canonical["exposure"]["gross"] == pytest.approx(0.9)
    assert canonical["exposure"]["net"] == pytest.approx(0.9)
    usd = next(item for item in canonical["targets"] if item["currency"] == "USD")
    assert usd["fxRateToBase"] == pytest.approx(7.1)
    assert usd["fxQuoteId"].startswith("USD/CNY")


def test_delta_has_unchanged_omission_and_explicit_cash_delta() -> None:
    canonical = canonicalize_target_instruction_v3(_instruction("delta"))

    assert canonical["targetSemantics"] == "DELTA"
    assert canonical["omittedInstrumentPolicy"] == "UNCHANGED"
    assert canonical["cashWeight"] is None
    assert canonical["cashWeightDelta"] == pytest.approx(-0.05)
    assert canonical["exposure"]["gross"] is None
    assert canonical["exposure"]["grossDelta"] == pytest.approx(0.07)
    assert canonical["exposure"]["netDelta"] == pytest.approx(0.03)


def test_cash_no_signal_and_revoke_are_three_distinct_protocol_states() -> None:
    cash = canonicalize_target_instruction_v3(_instruction("cashOnly"))
    no_signal = canonicalize_target_instruction_v3(_instruction("noSignal"))
    revoke = canonicalize_target_instruction_v3(_instruction("revoke"))

    assert cash["instructionType"] == "CASH_ONLY"
    assert cash["targetSemantics"] == "FULL_SNAPSHOT"
    assert cash["omittedInstrumentPolicy"] == "ZERO_TARGET"
    assert cash["cashWeight"] == 1.0
    assert no_signal["instructionType"] == "NO_SIGNAL"
    assert no_signal["omittedInstrumentPolicy"] == "UNCHANGED"
    assert no_signal["targets"] == []
    assert revoke["instructionType"] == "REVOKE"
    assert revoke["supersedesArtifactId"].startswith("art_")


@pytest.mark.parametrize("row", _cases()["compatibilityMatrix"])
def test_n_and_n_minus_one_version_matrix_is_explicit(row: dict) -> None:
    if "expected" in row:
        assert negotiate_contract_version(row["producer"], row["consumer"]) == row["expected"]
    else:
        with pytest.raises(ValueError, match=row["error"]):
            negotiate_contract_version(row["producer"], row["consumer"])


@pytest.mark.parametrize(
    ("producer", "consumer", "error"),
    [
        ([], ["3.0"], "non-empty"),
        (["3.0", "3.0"], ["3.0"], "duplicates"),
        (["3.0"], [""], "non-empty"),
    ],
)
def test_version_advertisements_fail_closed(producer: list[str], consumer: list[str], error: str) -> None:
    with pytest.raises(ValueError, match=error):
        negotiate_contract_version(producer, consumer)


def test_future_and_expired_instructions_are_rejected_at_observation_time() -> None:
    future = _instruction("fullSnapshot")
    with pytest.raises(ValueError, match="producedAt is in the future"):
        canonicalize_target_instruction_v3(future, observed_at=_observed("2026-09-14T15:05:30+08:00"))

    expired = _instruction("fullSnapshot")
    with pytest.raises(ValueError, match="expired"):
        canonicalize_target_instruction_v3(expired, observed_at=_observed("2026-09-15T16:00:00+08:00"))

    with pytest.raises(ValueError, match="timezone-aware"):
        canonicalize_target_instruction_v3(future, observed_at=datetime(2026, 9, 15, 10))


def test_execution_window_and_control_instructions_are_not_conflated() -> None:
    full = _instruction("fullSnapshot")
    with pytest.raises(ValueError, match="not executable yet"):
        assert_target_instruction_executable_at(full, at=_observed("2026-09-15T09:25:00+08:00"))
    assert assert_target_instruction_executable_at(full, at=_observed())["instructionType"] == "TARGETS"

    for name in ("noSignal", "revoke"):
        with pytest.raises(ValueError, match="control instruction"):
            assert_target_instruction_executable_at(_instruction(name), at=_observed())


def test_missing_stale_and_future_fx_are_rejected() -> None:
    missing = _instruction("fullSnapshot")
    usd = next(item for item in missing["targets"] if item["currency"] == "USD")
    usd.pop("fxQuoteId")
    with pytest.raises(ValueError, match="explicit FX"):
        canonicalize_target_instruction_v3(missing)

    future = _instruction("fullSnapshot")
    usd = next(item for item in future["targets"] if item["currency"] == "USD")
    usd["fxAvailableAt"] = "2026-09-14T16:00:00+08:00"
    with pytest.raises(ValueError, match="future information"):
        canonicalize_target_instruction_v3(future)

    stale = _instruction("fullSnapshot")
    stale["fx"]["maxAgeSeconds"] = 60
    with pytest.raises(ValueError, match="stale"):
        canonicalize_target_instruction_v3(stale)

    base_fx = _instruction("fullSnapshot")
    cny = next(item for item in base_fx["targets"] if item["currency"] == "CNY")
    cny.update(
        fxRateToBase=1.0,
        fxAvailableAt="2026-09-14T14:00:00+08:00",
        fxQuoteId="CNY/CNY",
    )
    with pytest.raises(ValueError, match="base-currency FX"):
        canonicalize_target_instruction_v3(base_fx)


def test_numeric_duplicate_and_risk_errors_fail_closed() -> None:
    duplicate = _instruction("fullSnapshot")
    duplicate["targets"].append(copy.deepcopy(duplicate["targets"][0]))
    with pytest.raises(ValueError, match="duplicate target"):
        canonicalize_target_instruction_v3(duplicate)

    nonfinite = _instruction("fullSnapshot")
    nonfinite["targets"][0]["targetWeight"] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        canonicalize_target_instruction_v3(nonfinite)

    out_of_range = _instruction("fullSnapshot")
    out_of_range["targets"][0]["targetWeight"] = 1.1
    with pytest.raises(ValueError, match="between -1 and 1"):
        canonicalize_target_instruction_v3(out_of_range)

    risk = _instruction("fullSnapshot")
    risk["risk"]["maxGrossExposure"] = 0.5
    with pytest.raises(ValueError, match="exceeds risk"):
        canonicalize_target_instruction_v3(risk)


def test_invalid_state_combinations_cannot_reuse_empty_targets_as_a_control_signal() -> None:
    empty_targets = _instruction("fullSnapshot")
    empty_targets["targets"] = []
    with pytest.raises(ValueError, match="TARGETS requires"):
        canonicalize_target_instruction_v3(empty_targets)

    cash = _instruction("cashOnly")
    cash["targets"] = [_instruction("fullSnapshot")["targets"][0]]
    with pytest.raises(ValueError, match="CASH_ONLY"):
        canonicalize_target_instruction_v3(cash)

    no_signal = _instruction("noSignal")
    no_signal["cashWeight"] = 1.0
    with pytest.raises(ValueError, match="NO_SIGNAL"):
        canonicalize_target_instruction_v3(no_signal)

    revoke = _instruction("revoke")
    revoke["supersedesArtifactId"] = None
    with pytest.raises(ValueError, match="requires supersedes"):
        canonicalize_target_instruction_v3(revoke)


def test_full_and_delta_omission_semantics_cannot_be_swapped() -> None:
    full = _instruction("fullSnapshot")
    full["omittedInstrumentPolicy"] = "UNCHANGED"
    with pytest.raises(ValueError, match="ZERO_TARGET"):
        canonicalize_target_instruction_v3(full)

    delta = _instruction("delta")
    delta["omittedInstrumentPolicy"] = "ZERO_TARGET"
    with pytest.raises(ValueError, match="UNCHANGED"):
        canonicalize_target_instruction_v3(delta)


def test_policy_calendar_universe_and_parent_identity_changes_are_not_equivalent() -> None:
    contract = canonicalize_target_instruction_v3(_instruction("fullSnapshot"))
    contract_identity = {
        "instructionType": contract["instructionType"],
        "targetSemantics": contract["targetSemantics"],
        "strategyPolicySha256": contract["policy"]["strategyPolicySha256"],
        "calendarVersionSha256": contract["calendar"]["versionSha256"],
    }
    kwargs = {
        "artifact_type": "TARGET_PORTFOLIO",
        "promotion_status": "RESEARCH_PROMOTED",
        "data_release_id": "ds_" + "a" * 64,
        "universe_release_id": "universe-fixture-v3",
        "source_manifest_sha256": "b" * 64,
        "payload_sha256": sha256_json(contract),
        "parent_artifact_ids": ["art_" + "c" * 64],
        "contract_identity": contract_identity,
    }
    artifact_id, identity = build_artifact_identity_v3(**kwargs)
    assert artifact_id.startswith("art_")
    assert identity["schemaVersion"] == "3.0"

    changed_policy = copy.deepcopy(contract_identity)
    changed_policy["strategyPolicySha256"] = "d" * 64
    assert build_artifact_identity_v3(**(kwargs | {"contract_identity": changed_policy}))[0] != artifact_id
    changed_calendar = copy.deepcopy(contract_identity)
    changed_calendar["calendarVersionSha256"] = "e" * 64
    assert build_artifact_identity_v3(**(kwargs | {"contract_identity": changed_calendar}))[0] != artifact_id
    assert build_artifact_identity_v3(**(kwargs | {"universe_release_id": "other-universe"}))[0] != artifact_id
    assert build_artifact_identity_v3(
        **(kwargs | {"parent_artifact_ids": ["art_" + "f" * 64]})
    )[0] != artifact_id


def test_artifact_identity_rejects_invalid_release_hashes_and_parents() -> None:
    base = {
        "artifact_type": "TARGET_PORTFOLIO",
        "promotion_status": "RESEARCH_PROMOTED",
        "data_release_id": "ds_" + "a" * 64,
        "universe_release_id": "universe-fixture-v3",
        "source_manifest_sha256": "b" * 64,
        "payload_sha256": "c" * 64,
        "parent_artifact_ids": ["art_" + "d" * 64],
    }
    with pytest.raises(ValueError, match="dataReleaseId"):
        build_artifact_identity_v3(**(base | {"data_release_id": "ds_not_hex"}))
    with pytest.raises(ValueError, match="sourceManifestSha256"):
        build_artifact_identity_v3(**(base | {"source_manifest_sha256": "bad"}))
    with pytest.raises(ValueError, match="parentArtifactIds"):
        build_artifact_identity_v3(
            **(base | {"parent_artifact_ids": ["art_" + "d" * 64, "art_" + "d" * 64]})
        )


def test_payload_references_are_portable_json_only_and_cannot_escape_bundle_root(tmp_path: Path) -> None:
    valid = {
        "mediaType": "application/json",
        "sha256": "a" * 64,
        "relativePath": "payloads/art_fixture.json",
    }
    assert validate_portable_payload_ref(valid, bundle_root=tmp_path) == "payloads/art_fixture.json"

    with pytest.raises(ValueError, match="mediaType"):
        validate_portable_payload_ref(valid | {"mediaType": "application/x-python-pickle"})
    with pytest.raises(ValueError, match="safe relative JSON"):
        validate_portable_payload_ref(valid | {"relativePath": "../escape.json"})
    with pytest.raises(ValueError, match="safe relative JSON"):
        validate_portable_payload_ref(valid | {"relativePath": "/absolute/payload.json"})
    with pytest.raises(ValueError, match="safe relative JSON"):
        validate_portable_payload_ref(valid | {"relativePath": "payloads/model.pkl"})
