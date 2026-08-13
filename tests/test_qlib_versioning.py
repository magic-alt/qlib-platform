from __future__ import annotations

import os
from pathlib import Path

from tushare_qlib.dataset_resolver import ResolvedDataset
from tushare_qlib.qlib_export import _clone_base_dataset, _deduplicate_unchanged


def _resolved(path: Path) -> ResolvedDataset:
    manifest = path / "dataset_manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    return ResolvedDataset("research-current", "v1", "test", path, manifest, "hash")


def test_candidate_copy_cannot_mutate_immutable_parent(tmp_path: Path):
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / "feature.bin").write_bytes(b"old")
    resolved = _resolved(parent)
    candidate = tmp_path / "candidate"

    _clone_base_dataset(resolved, candidate)
    (candidate / "feature.bin").write_bytes(b"new")

    assert (parent / "feature.bin").read_bytes() == b"old"


def test_unchanged_files_are_linked_only_after_candidate_is_complete(tmp_path: Path):
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / "same.bin").write_bytes(b"same")
    (parent / "changed.bin").write_bytes(b"old")
    resolved = _resolved(parent)
    candidate = tmp_path / "candidate"
    _clone_base_dataset(resolved, candidate)
    (candidate / "changed.bin").write_bytes(b"new")

    _deduplicate_unchanged(resolved, candidate)

    assert os.path.samefile(parent / "same.bin", candidate / "same.bin")
    assert not os.path.samefile(parent / "changed.bin", candidate / "changed.bin")
