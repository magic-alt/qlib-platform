from __future__ import annotations

import json
from pathlib import Path

import pytest

from qlib_platform.research_bundle_export import export_manifest_as_v2_bundle


def test_legacy_research_manifest_converts_to_v2_bundle(tmp_path: Path):
    source = tmp_path / "manifest.json"
    source.write_text(
        json.dumps(
            {
                "schemaVersion": "2.0",
                "externalRunId": "run-1",
                "runKind": "walk_forward",
                "dataset": {"semantic_contract": {"data_release_id": "ds_" + "a" * 64}},
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
    path = export_manifest_as_v2_bundle(
        source,
        tmp_path / "v2",
        git_commit="abc123",
        container_digest="sha256:" + "b" * 64,
    )
    payload = json.loads(path.read_text())
    assert payload["importType"] == "QLIB_RESEARCH_BUNDLE"
    assert {item["promotionStatus"] for item in payload["artifacts"]} == {"RESEARCH_PROMOTED"}
    validation = next(item for item in payload["artifacts"] if item["artifactType"] == "VALIDATION_RESULT")
    uploads = json.loads(path.with_name("qlib_research_bundle.v2.uploads.json").read_text())["uploads"]
    validation_payload = json.loads(Path(uploads[validation["payloadRef"]["objectKey"]]).read_text())
    assert len(validation_payload["sourceManifestSha256"]) == 64
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
            data_release_id="ds_" + "a" * 64,
        )
