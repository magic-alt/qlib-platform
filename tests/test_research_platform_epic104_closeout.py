from __future__ import annotations

import json
from pathlib import Path

from qlib_platform.artifacts.artifact_contract_v3 import (
    PREVIOUS_SCHEMA_VERSION,
    SCHEMA_VERSION,
)
from qlib_platform.backtesting.strategy_sdk import (
    BaselineStrategy,
    ShadowStrategy,
    strategy_registry,
)
from qlib_platform.data.sources.semantic import FETCH_STATUSES
from qlib_platform.research.contracts.research_profile import (
    RESEARCH_PROFILES,
    RESEARCH_PROFILE_SCHEMA_VERSION,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "research-platform-epic104.v1.json"


def _contract() -> dict[str, object]:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def test_epic104_children_are_closed_with_repository_evidence() -> None:
    contract = _contract()
    epic = contract["epic"]
    children = contract["children"]

    assert isinstance(epic, dict)
    assert isinstance(children, list)
    assert epic["issue"] == 104
    assert epic["status"] == "RESEARCH_SOFTWARE_COMPLETE"
    assert [child["issue"] for child in children] == list(range(105, 111))
    assert all(child["status"] == "COMPLETE" for child in children)

    for child in children:
        for path in child["evidence"]:
            assert (ROOT / path).is_file(), f"missing Epic #104 evidence path: {path}"


def test_epic104_governance_stays_research_only() -> None:
    contract = _contract()
    governance = contract["governance"]
    capabilities = contract["capabilities"]

    assert isinstance(governance, dict)
    assert isinstance(capabilities, list)
    permitted = set(governance["permittedModes"])
    forbidden = set(governance["forbiddenModes"])

    assert permitted == {"research", "backtest", "diagnostics", "shadow"}
    assert forbidden == {"paper", "live", "production"}
    assert permitted.isdisjoint(forbidden)
    assert governance["formalCandidatesAllowed"] is False
    assert governance["modelSelectionAllowed"] is False
    assert governance["publishingAuthorized"] is False
    assert governance["finalHoldoutAccessAllowed"] is False
    assert governance["brokerOmsAuthority"] is False
    assert governance["maximumPromotionState"] == "RESEARCH_PROMOTED"

    for capability in capabilities:
        assert set(capability["modes"]).isdisjoint(forbidden)
        assert capability["productionCertified"] is False


def test_epic104_contract_versions_track_runtime_contracts() -> None:
    contract = _contract()
    versions = contract["contracts"]

    assert isinstance(versions, dict)
    assert versions["artifactCurrent"] == SCHEMA_VERSION
    assert versions["artifactPreviousSupported"] == PREVIOUS_SCHEMA_VERSION
    assert versions["researchProfileSchemaVersion"] == RESEARCH_PROFILE_SCHEMA_VERSION
    assert versions["strategySdkSchemaVersion"] == "1.0"
    assert versions["quantHandoff"] == "target_portfolio_v1"


def test_epic104_research_profiles_cannot_authorize_execution() -> None:
    contract = _contract()
    capabilities = {capability["id"]: capability for capability in contract["capabilities"]}
    declared_profiles = set(capabilities["multi_asset_research_profiles"]["scope"])

    assert {"ashare_equity_v1", "ashare_etf_v1"}.issubset(declared_profiles)
    assert declared_profiles.issubset(RESEARCH_PROFILES)
    for profile_id in declared_profiles:
        profile = RESEARCH_PROFILES[profile_id]
        assert profile.promotion_scope == "research_only"
        assert set(profile.allowed_workflows) <= {"research", "backtest", "diagnostics"}


def test_epic104_strategy_sdk_keeps_baselines_and_shadow_separate() -> None:
    contract = _contract()
    capabilities = {capability["id"]: capability for capability in contract["capabilities"]}
    scope = set(capabilities["strategy_pipeline"]["scope"])
    registry = strategy_registry()

    assert {"topk_dropout_v1", "rank_buffer_v1", "score_threshold_shadow_v1"} <= scope
    assert isinstance(registry["topk_dropout_v1"], BaselineStrategy)
    assert isinstance(registry["rank_buffer_v1"], BaselineStrategy)
    assert isinstance(registry["score_threshold_shadow_v1"], ShadowStrategy)
    assert registry["score_threshold_shadow_v1"].mode == "shadow"


def test_epic104_data_source_failures_remain_auditable() -> None:
    required_failures = {
        "permission_denied",
        "rate_limited",
        "timeout",
        "unsupported",
        "schema_mismatch",
        "conflict",
        "provider_error",
    }

    assert required_failures <= FETCH_STATUSES


def test_epic104_execution_plane_remains_external_and_uncertified() -> None:
    contract = _contract()
    epic = contract["epic"]
    rollback = contract["rollbackCompatibility"]

    assert isinstance(epic, dict)
    assert isinstance(rollback, dict)
    paired = epic["pairedExecution"]
    assert paired == {
        "repository": "magic-alt/lean-local-platform",
        "issue": 58,
        "status": "EXTERNAL_EVIDENCE_PENDING",
        "productionCertified": False,
    }
    assert rollback["dataReleaseIdentityMutable"] is False
    assert rollback["historicalStrategyIdsMutable"] is False
    assert rollback["artifactV2ReinterpretedInPlace"] is False
    assert rollback["researchProfilesMayAuthorizeExecution"] is False
    assert rollback["executionAuthorityMayMoveIntoQlibPlatform"] is False
