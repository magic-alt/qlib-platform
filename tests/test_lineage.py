from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from qlib_platform import lineage as lineage_module
from qlib_platform.lineage import build_lineage, dirty_research_override_enabled, resolve_qlib_repo
from qlib_platform.settings import Paths, Settings
from qlib_platform.store import sha256_file


def _settings(tmp_path: Path, *, allow_dirty_research: bool = False) -> Settings:
    config_path = tmp_path / "configs" / "pipeline.yaml"
    config_path.parent.mkdir()
    config_path.write_text("project_root: ./data\n", encoding="utf-8")
    qlib_data = tmp_path / "qlib_data"
    qlib_data.mkdir()
    paths = Paths.from_root(tmp_path / "data")
    membership = paths.metadata / "universe_membership" / "csi300.parquet"
    membership.parent.mkdir(parents=True)
    membership.write_text("fixture", encoding="utf-8")
    (qlib_data / "dataset_manifest.json").write_text(
        json.dumps(
            {
                "qlib_git_commit": "qlib-commit",
                "source_snapshot_id": "snapshot-1",
                "universe_membership_sha256": sha256_file(membership),
            }
        ),
        encoding="utf-8",
    )
    qlib_repo = tmp_path / "qlib_repo"
    qlib_repo.mkdir()
    return Settings(
        config_path=config_path,
        data={
            "research": {"allow_dirty_research": allow_dirty_research},
            "universe": {"instruments": "csi300", "index_code": "399300.SZ"},
        },
        paths=paths,
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
    monkeypatch.setattr(lineage_module, "resolve_qlib_repo", lambda configured: settings.qlib_repo)

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


def test_dirty_override_rejects_mismatched_universe_membership(tmp_path: Path):
    settings = _settings(tmp_path, allow_dirty_research=True)
    lineage = {
        "requiredFieldsComplete": True,
        "qlibCommitMatchesDataset": True,
        "universeMembershipMatchesDataset": False,
        "qlibPlatformDirty": True,
        "qlibDirty": False,
    }

    assert dirty_research_override_enabled(settings, lineage) is False


def test_resolve_qlib_repo_falls_back_to_imported_editable_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    checkout = tmp_path / "qlib-checkout"
    package = checkout / "qlib"
    package.mkdir(parents=True)
    (checkout / ".git").mkdir()
    origin = package / "__init__.py"
    origin.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        lineage_module.importlib.util,
        "find_spec",
        lambda name: SimpleNamespace(origin=str(origin)) if name == "qlib" else None,
    )

    assert resolve_qlib_repo(tmp_path / "missing-configured-repo") == checkout


def test_resolve_qlib_repo_rejects_enclosing_application_repo_for_wheel_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    application = tmp_path / "qlib-platform"
    (application / ".git").mkdir(parents=True)
    package = application / ".venv" / "Lib" / "site-packages" / "qlib"
    package.mkdir(parents=True)
    origin = package / "__init__.py"
    origin.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        lineage_module.importlib.util,
        "find_spec",
        lambda name: SimpleNamespace(origin=str(origin)) if name == "qlib" else None,
    )

    assert resolve_qlib_repo(None) is None


def test_resolve_qlib_repo_rejects_stale_configured_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    configured = tmp_path / "stale-qlib"
    (configured / ".git").mkdir(parents=True)
    stale_package = configured / "qlib"
    stale_package.mkdir()
    (stale_package / "__init__.py").write_text("", encoding="utf-8")

    actual = tmp_path / "actual-qlib"
    package = actual / "qlib"
    package.mkdir(parents=True)
    (actual / ".git").mkdir()
    origin = package / "__init__.py"
    origin.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        lineage_module.importlib.util,
        "find_spec",
        lambda name: SimpleNamespace(origin=str(origin)) if name == "qlib" else None,
    )

    assert resolve_qlib_repo(configured) == actual
