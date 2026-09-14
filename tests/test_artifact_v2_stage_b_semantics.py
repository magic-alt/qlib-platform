from __future__ import annotations

import json
from pathlib import Path

import pytest

from qlib_platform.artifacts.institutional_artifacts import (
    ResearchBundleContext,
    ResearchPromotionStatus,
    export_research_bundle,
)


def _context() -> ResearchBundleContext:
    return ResearchBundleContext(
        external_run_id="stage-b-signal-semantics",
        run_kind="walk_forward",
        data_release_id="ds_" + "a" * 64,
        git_commit="fixture-commit",
        container_digest="sha256:" + "b" * 64,
        as_of_time="2026-08-14T00:00:00+08:00",
        signal_date="2026-08-13",
        trade_date="2026-08-14",
        universe_release_id="universe-csi300-fixture-v2",
        source_manifest_sha256="d" * 64,
    )


def _export(output: Path, *, signals: list[dict], targets: list[dict]) -> Path:
    return export_research_bundle(
        output,
        context=_context(),
        promotion_status=ResearchPromotionStatus.CANDIDATE,
        model={},
        strategy_policy={},
        signals=signals,
        targets=targets,
        validation={},
    )


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_signal_score_fails_before_bundle_creation(tmp_path: Path, invalid: float):
    output = tmp_path / "bundle"

    with pytest.raises(ValueError, match="Invalid signal score"):
        _export(
            output,
            signals=[{"instrument": "SH600000", "score": invalid}],
            targets=[{"instrument": "SH600000", "targetWeight": 0.1, "score": 0.5}],
        )

    assert not output.exists()


def test_malformed_signal_fails_before_bundle_creation(tmp_path: Path):
    output = tmp_path / "bundle"

    with pytest.raises(ValueError, match="Invalid signal instrument"):
        _export(
            output,
            signals=[{"instrument": "600000", "score": 0.5}],
            targets=[{"instrument": "SH600000", "targetWeight": 0.1, "score": 0.5}],
        )
    assert not output.exists()

    with pytest.raises(ValueError, match="Invalid signal score"):
        _export(
            output,
            signals=[{"instrument": "SH600000", "score": "not-a-number"}],
            targets=[{"instrument": "SH600000", "targetWeight": 0.1, "score": 0.5}],
        )
    assert not output.exists()


def test_duplicate_signal_and_malformed_target_weight_fail_before_writes(tmp_path: Path):
    output = tmp_path / "bundle"

    with pytest.raises(ValueError, match="Duplicate signal instrument"):
        _export(
            output,
            signals=[
                {"instrument": "SH600000", "score": 0.5},
                {"instrument": "sh600000", "score": 0.4},
            ],
            targets=[{"instrument": "SH600000", "targetWeight": 0.1, "score": 0.5}],
        )
    assert not output.exists()

    with pytest.raises(ValueError, match="Invalid target weight"):
        _export(
            output,
            signals=[],
            targets=[{"instrument": "SH600000", "targetWeight": "bad", "score": 0.5}],
        )
    assert not output.exists()


def test_valid_signal_payload_preserves_order_and_extra_fields(tmp_path: Path):
    output = tmp_path / "bundle"
    signals = [
        {"instrument": "SZ000001", "score": 0.4, "rank": 2},
        {"instrument": "SH600000", "score": 0.8, "rank": 1, "source": "alpha158"},
    ]

    manifest_path = _export(
        output,
        signals=signals,
        targets=[{"instrument": "SH600000", "targetWeight": 0.1, "score": 0.8}],
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    signal_artifact = next(
        item for item in manifest["artifacts"] if item["artifactType"] == "SIGNAL_SNAPSHOT"
    )
    uploads = json.loads(
        manifest_path.with_name("qlib_research_bundle.v2.uploads.json").read_text(encoding="utf-8")
    )["uploads"]
    signal_payload = json.loads(
        Path(uploads[signal_artifact["payloadRef"]["objectKey"]]).read_text(encoding="utf-8")
    )

    assert signal_payload == {"signals": signals}
