from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tushare_qlib import lineage as lineage_module
from tushare_qlib.lineage import build_lineage, dirty_research_override_enabled
from tushare_qlib.settings import Paths, Settings


def _settings(tmp_path: Path, *, allow_dirty_research: bool = False) -> Settings:
    config_path = tmp_path / "configs" / "pipeline.yaml"
    config_path.parent.mkdir()
    config_path.write_text("project_root: ./data\n", encoding="utf-8")
    qlib_data = tmp_path / "qlib_data"
    qlib_data.mkdir()
    (qlib_data / "dataset_manifest.json").write_text(
        json.dumps({"qlib_git_commit": "qlib-commit", "source_snapshot_id": "snapshot-1"}),
        encoding="utf-8",
    )
    qlib_repo = tmp_path / "qlib_repo"
    qlib_repo.mkdir()
    return Settings(
        config_path=config_path,
        data={"research": {"allow_dirty_research": allow_dirty_research}},
        paths=Paths.from_root(tmp_path / "data"),
        tushare_token=None,
        qlib_repo=qlib_repo,
        qlib_data_uri=qlib_data,
    )


def _canonical() -> SimpleNamespace:
    return SimpleNamespace(
        dataset=SimpleNamespace(
            universe_name="CSI300",
            membership_type="point_in_time",
            source="lean_mysql",
            secondary_filters={},
        ),
        model=SimpleNamespace(parameters={"num_leaves": 31}),
    )


@pytest.mark.parametrize(
    ("platform_dirty", "qlib_dirty"),
    [(True, False), (False, True), (None, False), (False, None)],
)
def test_lineage_fails_closed_for_dirty_or_unknown_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform_dirty: bool | None, qlib_dirty: bool | None
):
    settings = _settings(tmp_path)

    def revision(path: Path | None) -> dict[str, object]:
        if path == settings.qlib_repo:
            return {"commit": "qlib-commit", "dirty": qlib_dirty}
        return {"commit": "platform-commit", "dirty": platform_dirty}

    monkeypatch.setattr(lineage_module, "git_revision", revision)

    result = build_lineage(
        settings, _canonical(), dataset_fingerprint="dataset-1", feature_columns=["$close"]
    )

    assert result["requiredFieldsComplete"] is True
    assert result["qlibCommitMatchesDataset"] is True
    assert result["complete"] is False


def test_dirty_research_override_is_research_only_and_rejects_unknown_dirty(tmp_path: Path):
    settings = _settings(tmp_path, allow_dirty_research=True)
    lineage = {
        "requiredFieldsComplete": True,
        "qlibCommitMatchesDataset": True,
        "qlibPlatformDirty": True,
        "qlibDirty": False,
        "complete": False,
    }

    assert dirty_research_override_enabled(settings, lineage) is True
    assert lineage["complete"] is False

    lineage["qlibPlatformDirty"] = None
    assert dirty_research_override_enabled(settings, lineage) is False
