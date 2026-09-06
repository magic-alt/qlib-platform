from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from qlib_platform.releases import capabilities


def test_manifest_governance_level_prefers_policy_and_profiles() -> None:
    assert capabilities.manifest_governance_level({"policies": {"governanceLevel": "certified"}}) == "certified"
    assert capabilities.manifest_governance_level({"profile": capabilities.QLIB_IMPORT_PROFILE}) == "exploratory"
    assert capabilities.manifest_governance_level({"profile": capabilities.MARKET_IMPORT_PROFILE}) == "exploratory"
    assert capabilities.manifest_governance_level({"profile": "research"}) == "research"


def test_assert_manifest_capability_enforces_explicit_policies() -> None:
    manifest = {
        "dataReleaseId": "ds_" + "a" * 64,
        "profile": "research",
        "policies": {"phase2Allowed": True, "targetPortfolioAllowed": True},
    }
    capabilities.assert_manifest_capability(manifest, "phase2")
    capabilities.assert_manifest_capability(manifest, "target_portfolio")
    with pytest.raises(ValueError, match="unknown DataRelease capability"):
        capabilities.assert_manifest_capability(manifest, "unknown")
    forbidden = {**manifest, "policies": {"phase2Allowed": False}}
    with pytest.raises(capabilities.ReleaseCapabilityError, match="forbids"):
        capabilities.assert_manifest_capability(forbidden, "phase2")
    exploratory = {"dataReleaseId": "ds_" + "b" * 64, "profile": capabilities.QLIB_IMPORT_PROFILE}
    with pytest.raises(capabilities.ReleaseCapabilityError, match="exploratory"):
        capabilities.assert_manifest_capability(exploratory, "phase3")
    capabilities.assert_manifest_capability({"profile": "research"}, "phase3")


def test_require_release_capability_resolves_alias_and_config(monkeypatch, tmp_path) -> None:
    release = SimpleNamespace(manifest={"profile": "research", "policies": {"phase3Allowed": True}})
    resolved: list[str] = []

    class Registry:
        def __init__(self, _: object):
            pass

        def resolve_release_alias(self, alias: str) -> str:
            assert alias == "research-release-current"
            return "ds_alias"

    class Store:
        def __init__(self, _: object):
            pass

        def resolve(self, reference: str, **kwargs: object) -> object:
            resolved.append(reference)
            assert kwargs["mode"] == "deep"
            return release

    settings = SimpleNamespace(
        registry_path=tmp_path / "registry.sqlite",
        uses_data_release=lambda: False,
        data_release_config={},
    )
    monkeypatch.setattr(capabilities, "DatasetRegistry", Registry)
    monkeypatch.setattr(capabilities, "FileReleaseStore", Store)
    monkeypatch.setattr(capabilities, "release_store_root", lambda _: tmp_path)
    assert capabilities.require_release_capability(settings, "phase3") is release
    assert resolved == ["ds_alias"]

    class EmptyRegistry(Registry):
        def resolve_release_alias(self, alias: str) -> None:
            return None

    monkeypatch.setattr(capabilities, "DatasetRegistry", EmptyRegistry)
    settings.uses_data_release = lambda: True
    settings.data_release_config = {"id": "ds_config"}
    assert capabilities.require_release_capability(settings, "phase3") is release
    assert resolved[-1] == "ds_config"
    settings.uses_data_release = lambda: False
    settings.data_release_config = {}
    with pytest.raises(capabilities.ReleaseCapabilityError, match="explicitly bound"):
        capabilities.require_release_capability(settings, "phase3")


def test_data_release_id_from_bundle_validation(tmp_path) -> None:
    release_id = "ds_" + "a" * 64
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps({"artifacts": [{"dataReleaseId": release_id}, {"dataReleaseId": release_id}]}))
    assert capabilities.data_release_id_from_bundle(path) == release_id
    path.write_text("[]")
    with pytest.raises(capabilities.ReleaseCapabilityError, match="JSON object"):
        capabilities.data_release_id_from_bundle(path)
    path.write_text(json.dumps({"artifacts": []}))
    with pytest.raises(capabilities.ReleaseCapabilityError, match="no artifacts"):
        capabilities.data_release_id_from_bundle(path)
    path.write_text(json.dumps({"artifacts": [{"dataReleaseId": release_id}, {"dataReleaseId": "ds_" + "b" * 64}]}))
    with pytest.raises(capabilities.ReleaseCapabilityError, match="exactly one"):
        capabilities.data_release_id_from_bundle(path)
    path.write_text(json.dumps({"artifacts": [{"dataReleaseId": "bad"}]}))
    with pytest.raises(capabilities.ReleaseCapabilityError, match="invalid"):
        capabilities.data_release_id_from_bundle(path)
