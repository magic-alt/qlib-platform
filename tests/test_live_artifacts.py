from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from tushare_qlib.artifact_resolver import ArtifactResolutionError, ArtifactResolver, sha256_path
from tushare_qlib.artifacts import ArtifactType
from tushare_qlib.live_artifacts import payload_sha256, stamp_live_artifact, validate_live_artifact


def test_portable_signal_uri_resolves_after_root_changes(tmp_path: Path):
    first = tmp_path / "computer-a" / "signal" / "signal-1"
    first.mkdir(parents=True)
    attestation = first / "attestation.json"
    attestation.write_text("{}", encoding="utf-8")
    uri = ArtifactResolver.signal_uri("signal-1", "attestation.json")
    resolver = ArtifactResolver(roots={"signal": tmp_path / "computer-a" / "signal"})
    assert resolver.resolve(uri) == attestation.resolve()

    second = tmp_path / "computer-b" / "signal" / "signal-1"
    second.mkdir(parents=True)
    moved = second / "attestation.json"
    moved.write_text("{}", encoding="utf-8")
    other = ArtifactResolver(roots={"signal": tmp_path / "computer-b" / "signal"})
    assert other.resolve(uri) == moved.resolve()
    with pytest.raises(ArtifactResolutionError, match="checksum"):
        other.resolve(uri, expected_sha256="wrong")


def test_live_score_requires_matching_attestation(tmp_path: Path):
    root = tmp_path / "signals"
    folder = root / "signal-1"
    folder.mkdir(parents=True)
    core = pd.DataFrame(
        {
            "signal_date": ["2026-08-10"],
            "trade_date": ["2026-08-11"],
            "instrument": ["SH600000"],
            "score": [0.5],
        }
    )
    digest = payload_sha256(core)
    attestation = folder / "attestation.json"
    attestation.write_text(
        json.dumps(
            {
                "signalId": "signal-1",
                "deploymentId": "model-1",
                "datasetSha256": "dataset-1",
                "signalSha256": digest,
            }
        ),
        encoding="utf-8",
    )
    uri = ArtifactResolver.signal_uri("signal-1", "attestation.json")
    governed = stamp_live_artifact(
        core,
        ArtifactType.MODEL_SCORE,
        deployment_id="model-1",
        dataset_sha256="dataset-1",
        signal_id="signal-1",
        manifest_uri=uri,
        manifest_sha256=sha256_path(attestation),
    )
    resolver = ArtifactResolver(roots={"signal": root})
    assert (
        validate_live_artifact(governed, ArtifactType.MODEL_SCORE, resolver=resolver)["deployment_id"]
        == "model-1"
    )
    governed.loc[0, "score"] = 0.9
    with pytest.raises(ValueError, match="checksum"):
        validate_live_artifact(governed, ArtifactType.MODEL_SCORE, resolver=resolver)
