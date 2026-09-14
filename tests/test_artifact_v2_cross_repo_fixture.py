from __future__ import annotations

import hashlib
import json
from pathlib import Path

from qlib_platform.artifacts.research_bundle_export import export_manifest_as_v2_bundle


FIXTURES = Path(__file__).parent / "fixtures" / "artifact_v2"
GOLDEN = FIXTURES / "golden_v2"
CONTAINER_DIGEST = "sha256:" + "b" * 64


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_cross_repo_golden_bundle_regenerates_byte_for_byte(tmp_path: Path):
    lock = json.loads((GOLDEN / "fixture.lock.json").read_text(encoding="utf-8"))
    source = FIXTURES / "producer_valid.json"

    assert _sha256(source) == lock["sourceManifestSha256"]
    assert _sha256(GOLDEN / lock["bundlePath"]) == lock["bundleSha256"]

    generated = export_manifest_as_v2_bundle(
        source,
        tmp_path / "generated",
        git_commit="fixture-commit-stage-b",
        container_digest=CONTAINER_DIGEST,
    )

    golden_bundle = GOLDEN / "qlib_research_bundle.v2.json"
    assert generated.read_bytes() == golden_bundle.read_bytes()

    bundle = json.loads(generated.read_text(encoding="utf-8"))
    assert bundle["schemaVersion"] == lock["contractVersion"]
    assert bundle["externalRunId"] == lock["externalRunId"]
    assert {item["dataReleaseId"] for item in bundle["artifacts"]} == {
        lock["dataReleaseId"]
    }
    assert {item["universeReleaseId"] for item in bundle["artifacts"]} == {
        lock["universeReleaseId"]
    }

    expected_files = {"qlib_research_bundle.v2.json"}
    for artifact in bundle["artifacts"]:
        relative = f"payloads/{artifact['artifactId']}.json"
        expected_files.add(relative)
        generated_payload = generated.parent / relative
        golden_payload = GOLDEN / relative
        assert generated_payload.read_bytes() == golden_payload.read_bytes()
        assert _sha256(golden_payload) == lock["files"][relative]

    assert set(lock["files"]) == expected_files
    assert _sha256(golden_bundle) == lock["files"]["qlib_research_bundle.v2.json"]
