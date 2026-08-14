from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tushare_qlib.institutional_artifacts import (
    ResearchArtifactType,
    ResearchBundleContext,
    ResearchPromotionStatus,
    export_research_bundle,
)


def _context() -> ResearchBundleContext:
    return ResearchBundleContext(
        external_run_id="run-20260814",
        run_kind="walk_forward",
        data_release_id="ds_" + "a" * 64,
        git_commit="abc123",
        container_digest="sha256:" + "b" * 64,
        as_of_time="2026-08-14T00:00:00+08:00",
        signal_date="2026-08-13",
        trade_date="2026-08-14",
        universe_release_id="universe-csi300-v1",
    )


def test_export_bundle_is_content_addressed_and_research_only(tmp_path: Path):
    path = export_research_bundle(
        tmp_path,
        context=_context(),
        promotion_status=ResearchPromotionStatus.RESEARCH_PROMOTED,
        model={"family": "lightgbm", "parameters": {"seed": 42}},
        strategy_policy={"topk": 30, "nDrop": 5},
        signals=[{"instrument": "SH600000", "score": 0.8}],
        targets=[{"instrument": "SH600000", "targetWeight": 0.08, "score": 0.8}],
        validation={"metrics": {"icir": 0.283, "rankIcir": 0.51}},
    )
    payload = json.loads(path.read_text())
    uploads = json.loads(path.with_name("qlib_research_bundle.v2.uploads.json").read_text())["uploads"]
    artifacts = payload["artifacts"]
    types = {item["artifactType"] for item in artifacts}

    assert types == {item.value for item in ResearchArtifactType}
    assert not {"ORDER_INTENT", "BROKER_ORDER", "FILL"} & types
    assert payload["rootArtifactIds"] == [artifacts[-1]["artifactId"]]
    assert artifacts[0]["modelReleaseId"] == artifacts[0]["artifactId"]
    assert artifacts[1]["strategyPolicyId"] == artifacts[1]["artifactId"]
    for item in artifacts:
        local = Path(uploads[item["payloadRef"]["objectKey"]])
        assert hashlib.sha256(local.read_bytes()).hexdigest() == item["payloadSha256"]
        assert item["dataReleaseId"] == _context().data_release_id
        assert "localPath" not in item["payloadRef"]


def test_export_bundle_rejects_execution_date_and_unsafe_target(tmp_path: Path):
    invalid_context = ResearchBundleContext(**{**_context().__dict__, "trade_date": "2026-08-13"})
    with pytest.raises(ValueError, match="trade_date must be after"):
        export_research_bundle(
            tmp_path,
            context=invalid_context,
            promotion_status=ResearchPromotionStatus.CANDIDATE,
            model={},
            strategy_policy={},
            signals=[],
            targets=[{"instrument": "SH600000", "targetWeight": 0.1}],
            validation={},
        )

    with pytest.raises(ValueError, match="gross exposure"):
        export_research_bundle(
            tmp_path,
            context=_context(),
            promotion_status=ResearchPromotionStatus.CANDIDATE,
            model={},
            strategy_policy={},
            signals=[],
            targets=[
                {"instrument": "SH600000", "targetWeight": 0.6},
                {"instrument": "SZ000001", "targetWeight": 0.6},
            ],
            validation={},
        )
