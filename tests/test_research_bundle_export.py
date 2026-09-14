from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from qlib_platform.artifacts.research_bundle_export import (
    export_manifest_as_v2_bundle,
    resolve_data_release_id,
    resolve_universe_release_id,
)


FIXTURES = Path(__file__).parent / "fixtures" / "artifact_v2"
DATA_RELEASE_A = "ds_" + "a" * 64
DATA_RELEASE_B = "ds_" + "b" * 64
UNIVERSE_RELEASE_A = "universe-csi300-fixture-v2"
UNIVERSE_RELEASE_B = "universe-csi300-conflict-v2"


def test_legacy_research_manifest_converts_to_v2_bundle(tmp_path: Path):
    source = tmp_path / "manifest.json"
    source.write_text(
        json.dumps(
            {
                "schemaVersion": "2.0",
                "externalRunId": "run-1",
                "runKind": "walk_forward",
                "dataset": {
                    "universeReleaseId": UNIVERSE_RELEASE_A,
                    "semantic_contract": {
                        "data_release_id": DATA_RELEASE_A,
                        "universe_release_id": UNIVERSE_RELEASE_A,
                    },
                },
                "model": {"fingerprint": "model-1", "family": "lightgbm"},
                "canonicalConfig": {
                    "strategy": {"topk": 30},
                    "portfolio": {"max_exposure": 0.9},
                    "promotion": {"min_icir": 0.5},
                },
                "promotion": {"status": "PROMOTED", "decision": "PASS"},
                "metrics": {"icir": 0.283, "rank_icir": 0.51},
                "latestTargets": {
                    "signalDate": "2026-08-13",
                    "tradeDate": "2026-08-14",
                    "targets": [{"instrument": "SH600000", "targetWeight": 0.08, "score": 0.7}],
                },
            }
        ),
        encoding="utf-8",
    )
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    path = export_manifest_as_v2_bundle(
        source,
        tmp_path / "v2",
        git_commit="abc123",
        container_digest="sha256:" + "b" * 64,
    )
    payload = json.loads(path.read_text())
    assert payload["importType"] == "QLIB_RESEARCH_BUNDLE"
    assert {item["promotionStatus"] for item in payload["artifacts"]} == {"RESEARCH_PROMOTED"}
    assert {item["universeReleaseId"] for item in payload["artifacts"]} == {UNIVERSE_RELEASE_A}
    assert {
        item["metadata"]["sourceManifestSha256"] for item in payload["artifacts"]
    } == {source_sha256}
    validation = next(item for item in payload["artifacts"] if item["artifactType"] == "VALIDATION_RESULT")
    uploads = json.loads(path.with_name("qlib_research_bundle.v2.uploads.json").read_text())["uploads"]
    validation_payload = json.loads(Path(uploads[validation["payloadRef"]["objectKey"]]).read_text())
    assert validation_payload["sourceManifestSha256"] == source_sha256
    assert str(source) not in path.read_text()
    assert str(source) not in json.dumps(validation_payload)


def test_conversion_requires_promotable_targets(tmp_path: Path):
    source = tmp_path / "manifest.json"
    source.write_text(json.dumps({"externalRunId": "run-1"}), encoding="utf-8")
    with pytest.raises(ValueError, match="latestTargets"):
        export_manifest_as_v2_bundle(
            source,
            tmp_path / "v2",
            git_commit="abc123",
            container_digest="sha256:" + "b" * 64,
            data_release_id=DATA_RELEASE_A,
        )


def test_release_resolution_accepts_one_identity_repeated_across_sources():
    manifest = {
        "dataset": {
            "dataReleaseId": DATA_RELEASE_A,
            "semantic_contract": {"data_release_id": DATA_RELEASE_A},
        },
        "canonicalConfig": {"dataset": {"dataset_id": DATA_RELEASE_A}},
    }

    assert resolve_data_release_id(manifest, None) == DATA_RELEASE_A
    assert resolve_data_release_id(manifest, DATA_RELEASE_A) == DATA_RELEASE_A


def test_universe_resolution_accepts_one_identity_repeated_across_sources():
    manifest = {
        "dataset": {
            "universeReleaseId": UNIVERSE_RELEASE_A,
            "semantic_contract": {"universe_release_id": UNIVERSE_RELEASE_A},
        },
        "canonicalConfig": {"dataset": {"universe_release_id": UNIVERSE_RELEASE_A}},
    }

    assert resolve_universe_release_id(manifest) == UNIVERSE_RELEASE_A


def test_release_override_only_fills_missing_identity():
    assert resolve_data_release_id({}, DATA_RELEASE_A) == DATA_RELEASE_A


@pytest.mark.parametrize(
    "release_id",
    [
        "ds_" + "g" * 64,
        "ds_" + "A" * 64,
        "ds_short",
    ],
)
def test_release_resolution_rejects_malformed_identity(release_id: str):
    with pytest.raises(ValueError, match="expected ds_<64 lowercase hex>"):
        resolve_data_release_id({"dataset": {"dataReleaseId": release_id}}, None)


def test_conflicting_manifest_release_id_fails_without_partial_bundle(tmp_path: Path):
    manifest = json.loads((FIXTURES / "producer_valid.json").read_text(encoding="utf-8"))
    manifest["dataset"]["dataReleaseId"] = DATA_RELEASE_B
    source = tmp_path / "manifest.json"
    source.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "v2"

    with pytest.raises(ValueError, match="conflicting DataRelease identities"):
        export_manifest_as_v2_bundle(
            source,
            output,
            git_commit="fixture-commit",
            container_digest="sha256:" + "c" * 64,
        )

    assert not output.exists()


def test_conflicting_manifest_universe_release_fails_without_partial_bundle(tmp_path: Path):
    manifest = json.loads((FIXTURES / "producer_valid.json").read_text(encoding="utf-8"))
    manifest["canonicalConfig"]["dataset"] = {"universe_release_id": UNIVERSE_RELEASE_B}
    source = tmp_path / "manifest.json"
    source.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "v2"

    with pytest.raises(ValueError, match="conflicting UniverseRelease identities"):
        export_manifest_as_v2_bundle(
            source,
            output,
            git_commit="fixture-commit",
            container_digest="sha256:" + "c" * 64,
        )

    assert not output.exists()


def test_conflicting_override_fails_without_partial_bundle(tmp_path: Path):
    output = tmp_path / "v2"

    with pytest.raises(ValueError, match="--data-release-id conflicts"):
        export_manifest_as_v2_bundle(
            FIXTURES / "producer_valid.json",
            output,
            git_commit="fixture-commit",
            container_digest="sha256:" + "c" * 64,
            data_release_id=DATA_RELEASE_B,
        )

    assert not output.exists()


def test_frozen_v2_positive_fixture_preserves_producer_contract(tmp_path: Path):
    output = tmp_path / "v2"
    source = FIXTURES / "producer_valid.json"
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    path = export_manifest_as_v2_bundle(
        source,
        output,
        git_commit="fixture-commit",
        container_digest="sha256:" + "b" * 64,
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schemaVersion"] == "2.0"
    assert payload["importType"] == "QLIB_RESEARCH_BUNDLE"
    assert payload["externalRunId"] == "fixture-run-v2"
    assert [item["artifactType"] for item in payload["artifacts"]] == [
        "MODEL_RELEASE",
        "STRATEGY_POLICY",
        "SIGNAL_SNAPSHOT",
        "TARGET_PORTFOLIO",
        "VALIDATION_RESULT",
    ]
    assert {item["dataReleaseId"] for item in payload["artifacts"]} == {DATA_RELEASE_A}
    assert {item["universeReleaseId"] for item in payload["artifacts"]} == {UNIVERSE_RELEASE_A}
    assert {
        item["metadata"]["sourceManifestSha256"] for item in payload["artifacts"]
    } == {source_sha256}
    assert {item["promotionStatus"] for item in payload["artifacts"]} == {"RESEARCH_PROMOTED"}
    assert payload["rootArtifactIds"] == [payload["artifacts"][-1]["artifactId"]]

    uploads = json.loads(path.with_name("qlib_research_bundle.v2.uploads.json").read_text(encoding="utf-8"))[
        "uploads"
    ]
    assert len(uploads) == 5
    for artifact in payload["artifacts"]:
        object_key = artifact["payloadRef"]["objectKey"]
        assert object_key in uploads
        assert Path(uploads[object_key]).is_file()


@pytest.mark.parametrize(
    ("fixture", "message"),
    [
        ("producer_invalid_missing_targets.json", "latestTargets"),
        ("producer_invalid_same_day_trade.json", "trade_date must be after"),
        ("producer_invalid_gross_exposure.json", "gross exposure"),
    ],
)
def test_frozen_v2_negative_fixtures_fail_without_partial_bundle(
    tmp_path: Path,
    fixture: str,
    message: str,
):
    output = tmp_path / fixture.removesuffix(".json")

    with pytest.raises(ValueError, match=message):
        export_manifest_as_v2_bundle(
            FIXTURES / fixture,
            output,
            git_commit="fixture-commit",
            container_digest="sha256:" + "b" * 64,
        )

    assert not output.exists()
