from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from qlib_platform.artifacts.artifact_contract_v3 import canonicalize_target_instruction_v3
from qlib_platform.artifacts.research_bundle_export import (
    V3_ARTIFACT_TYPES,
    V3_MANIFEST_NAME,
    export_manifest_as_v3_bundle,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "artifact_v3"
SOURCE_MANIFEST = FIXTURE_ROOT / "producer_manifest_v3.json"
GOLDEN_DIGESTS = FIXTURE_ROOT / "producer_golden_sha256.json"
GIT_COMMIT = "a" * 40
CONTAINER_DIGEST = "sha256:" + "b" * 64


def _load_source() -> dict[str, object]:
    return json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))


def _write_source(path: Path, value: dict[str, object]) -> Path:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _bundle_snapshot(root: Path) -> dict[str, object]:
    rows: dict[str, dict[str, object]] = {}
    hasher = hashlib.sha256()
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        relative = path.relative_to(root).as_posix()
        payload = path.read_bytes()
        rows[relative] = {
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
        }
        hasher.update(relative.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(payload)
    return {"bundleSha256": hasher.hexdigest(), "files": rows}


def _export(source: Path, output: Path, *, git_commit: str = GIT_COMMIT) -> Path:
    return export_manifest_as_v3_bundle(
        source,
        output,
        git_commit=git_commit,
        container_digest=CONTAINER_DIGEST,
    )


def test_producer_fixture_reuses_the_shared_contract_case() -> None:
    source = _load_source()
    shared = json.loads((FIXTURE_ROOT / "contract_cases.json").read_text(encoding="utf-8"))
    assert source["artifactContractV3"]["targetInstruction"] == shared["instructions"]["fullSnapshot"]


def test_v3_export_is_byte_locked_portable_and_idempotent(tmp_path: Path) -> None:
    output = tmp_path / "bundle"
    manifest_path = _export(SOURCE_MANIFEST, output)
    assert manifest_path == output / V3_MANIFEST_NAME

    golden = json.loads(GOLDEN_DIGESTS.read_text(encoding="utf-8"))
    assert _bundle_snapshot(output) == golden

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schemaVersion"] == "3.0"
    assert manifest["artifactContractVersion"] == "3.0"
    assert manifest["status"] == "RESEARCH_PROMOTED"
    assert manifest["producer"] == "qlib-platform"
    assert manifest["codeCommit"] == GIT_COMMIT
    assert manifest["containerDigest"] == CONTAINER_DIGEST
    assert manifest["dataReleaseId"] == "ds_" + "1" * 64
    assert manifest["universeReleaseId"] == "universe-release-v3-fixture"
    assert tuple(row["type"] for row in manifest["signedArtifacts"]) == V3_ARTIFACT_TYPES

    artifact_ids = manifest["artifactIdsByType"]
    for row in manifest["signedArtifacts"]:
        artifact_path = output / row["relativePath"]
        envelope = json.loads(artifact_path.read_text(encoding="utf-8"))
        assert envelope["artifactVersion"] == "3.0"
        assert envelope["artifactId"] == artifact_ids[row["type"]]
        assert envelope["status"] == "RESEARCH_PROMOTED"
        assert envelope["producer"] == "qlib-platform"
        assert envelope["codeCommit"] == GIT_COMMIT
        assert envelope["containerDigest"] == CONTAINER_DIGEST
        assert envelope["sourceManifestSha256"] == manifest["sourceManifestSha256"]
        assert envelope["payload"]["mediaType"] == "application/json"
        assert not Path(envelope["payload"]["relativePath"]).is_absolute()
        payload = output / envelope["payload"]["relativePath"]
        assert hashlib.sha256(payload.read_bytes()).hexdigest() == envelope["payloadSha256"]

    first_mtime = manifest_path.stat().st_mtime_ns
    first_snapshot = _bundle_snapshot(output)
    assert _export(SOURCE_MANIFEST, output) == manifest_path
    assert manifest_path.stat().st_mtime_ns == first_mtime
    assert _bundle_snapshot(output) == first_snapshot

    all_bytes = b"".join(path.read_bytes() for path in output.rglob("*") if path.is_file())
    assert str(SOURCE_MANIFEST.resolve()).encode("utf-8") not in all_bytes
    assert b"pickle" not in all_bytes.lower()


@pytest.mark.parametrize("case_name", ["delta", "cashOnly", "noSignal", "revoke"])
def test_v3_export_supports_each_shared_instruction_state(
    case_name: str,
    tmp_path: Path,
) -> None:
    source = _load_source()
    shared = json.loads((FIXTURE_ROOT / "contract_cases.json").read_text(encoding="utf-8"))
    source["artifactContractV3"]["targetInstruction"] = shared["instructions"][case_name]
    source_path = _write_source(tmp_path / f"{case_name}.json", source)

    manifest_path = _export(source_path, tmp_path / f"bundle-{case_name}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    target_id = manifest["artifactIdsByType"]["TARGET_PORTFOLIO"]
    target_envelope = json.loads(
        (manifest_path.parent / f"artifacts/{target_id}.json").read_text(encoding="utf-8")
    )
    target_payload = json.loads(
        (manifest_path.parent / target_envelope["payload"]["relativePath"]).read_text(encoding="utf-8")
    )
    expected = canonicalize_target_instruction_v3(shared["instructions"][case_name])
    assert target_payload == expected


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing-contract", "artifactContractV3"),
        ("review-pending", "promotion.status"),
        ("missing-data-release", "not bound to a DataRelease"),
        ("missing-universe-release", "UniverseRelease"),
        ("bad-parent-shape", "parentArtifactIds"),
        ("bad-contract-version", "schemaVersion"),
        ("future-fx", "future information"),
    ],
)
def test_invalid_v3_sources_fail_before_final_output(
    mutation: str,
    message: str,
    tmp_path: Path,
) -> None:
    source = _load_source()
    if mutation == "missing-contract":
        source.pop("artifactContractV3")
    elif mutation == "review-pending":
        source["promotion"]["status"] = "RESEARCH_REVIEW"
    elif mutation == "missing-data-release":
        source["dataset"].pop("dataReleaseId")
        source["dataset"]["semantic_contract"].pop("data_release_id")
    elif mutation == "missing-universe-release":
        source["dataset"].pop("universeReleaseId")
        source["dataset"]["semantic_contract"].pop("universe_release_id")
    elif mutation == "bad-parent-shape":
        source["artifactContractV3"]["parentArtifactIds"] = "not-an-array"
    elif mutation == "bad-contract-version":
        source["artifactContractV3"]["schemaVersion"] = "4.0"
    else:
        source["artifactContractV3"]["targetInstruction"]["targets"][1]["fxAvailableAt"] = (
            "2026-09-14T15:01:00+08:00"
        )

    source_path = _write_source(tmp_path / "invalid.json", source)
    output = tmp_path / "bundle"
    with pytest.raises(ValueError, match=message):
        _export(source_path, output)
    assert not output.exists()
    assert not any(tmp_path.glob(".bundle.*"))


def test_invalid_runtime_identity_and_invalid_json_fail_without_output(
    tmp_path: Path,
) -> None:
    output = tmp_path / "bundle"
    with pytest.raises(ValueError, match="git_commit"):
        export_manifest_as_v3_bundle(
            SOURCE_MANIFEST,
            output,
            git_commit="HEAD",
            container_digest=CONTAINER_DIGEST,
        )
    with pytest.raises(ValueError, match="container_digest"):
        export_manifest_as_v3_bundle(
            SOURCE_MANIFEST,
            output,
            git_commit=GIT_COMMIT,
            container_digest="latest",
        )
    assert not output.exists()

    invalid_json = tmp_path / "invalid.json"
    invalid_json.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="valid JSON"):
        _export(invalid_json, output)
    assert not output.exists()


def test_existing_conflicting_bundle_is_never_deleted_or_repaired(
    tmp_path: Path,
) -> None:
    output = tmp_path / "bundle"
    output.mkdir()
    sentinel = output / "sentinel.txt"
    sentinel.write_text("consumer-owned-state", encoding="utf-8")

    with pytest.raises(FileExistsError, match="non-identical"):
        _export(SOURCE_MANIFEST, output)

    assert sentinel.read_text(encoding="utf-8") == "consumer-owned-state"
    assert list(output.iterdir()) == [sentinel]


def test_lineage_and_code_commit_changes_create_different_artifact_ids(
    tmp_path: Path,
) -> None:
    manifest_a = json.loads(_export(SOURCE_MANIFEST, tmp_path / "bundle-a").read_text(encoding="utf-8"))

    changed = _load_source()
    changed_release = "ds_" + "2" * 64
    changed["dataset"]["dataReleaseId"] = changed_release
    changed["dataset"]["semantic_contract"]["data_release_id"] = changed_release
    changed_path = _write_source(tmp_path / "changed.json", changed)
    manifest_b = json.loads(_export(changed_path, tmp_path / "bundle-b").read_text(encoding="utf-8"))
    manifest_c = json.loads(
        _export(
            SOURCE_MANIFEST,
            tmp_path / "bundle-c",
            git_commit="c" * 40,
        ).read_text(encoding="utf-8")
    )

    assert manifest_a["artifactIdsByType"] != manifest_b["artifactIdsByType"]
    assert manifest_a["artifactIdsByType"] != manifest_c["artifactIdsByType"]


def test_v3_export_never_promotes_beyond_research_promoted(tmp_path: Path) -> None:
    source = _load_source()
    source["promotion"]["status"] = "LEAN_VALIDATED"
    source_path = _write_source(tmp_path / "invalid-authority.json", source)
    output = tmp_path / "bundle"

    with pytest.raises(ValueError, match="promotion.status"):
        _export(source_path, output)
    assert not output.exists()
