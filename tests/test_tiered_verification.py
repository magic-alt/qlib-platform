from __future__ import annotations

import json
from pathlib import Path

import pytest

from tushare_qlib.dataset_manifest import verify_dataset_manifest, write_dataset_manifest
from tushare_qlib.releases import FileReleaseStore, LocalReleasePublisher


def _dataset(tmp_path: Path) -> Path:
    root = tmp_path / "dataset"
    (root / "features" / "sh600000").mkdir(parents=True)
    (root / "features" / "sh600000" / "close.day.bin").write_bytes(b"close")
    (root / "calendars").mkdir()
    (root / "calendars" / "day.txt").write_text("2026-08-24\n", encoding="utf-8")
    manifest, _ = write_dataset_manifest(
        root,
        dataset_name="test",
        layer="qlib",
        semantic_contract={"pit": "next_trading_day"},
    )
    return manifest


def _provider(root: Path) -> Path:
    (root / "calendars").mkdir(parents=True)
    (root / "instruments").mkdir()
    (root / "features" / "sh600000").mkdir(parents=True)
    (root / "calendars" / "day.txt").write_text("2026-08-24\n", encoding="utf-8")
    (root / "instruments" / "all.txt").write_text("SH600000\t2026-08-24\t2026-08-24\n", encoding="utf-8")
    (root / "features" / "sh600000" / "close.day.bin").write_bytes(b"close")
    return root


def test_dataset_modes_and_deep_receipt_are_explicit(tmp_path: Path):
    manifest = _dataset(tmp_path)
    receipts = tmp_path / "receipts"
    evidence: dict[str, object] = {}

    verify_dataset_manifest(manifest, mode="deep", receipt_dir=receipts, evidence=evidence)

    assert evidence["mode"] == "deep"
    assert evidence["verifiedFileCount"] == 2
    assert Path(str(evidence["receipt"])).is_file()

    reused: dict[str, object] = {}
    verify_dataset_manifest(
        manifest,
        mode="deep",
        receipt_dir=receipts,
        reuse_receipt=True,
        evidence=reused,
    )
    assert reused["verificationSource"] == "receipt"
    assert reused["verifiedFileCount"] == 0


def test_manifest_mode_is_metadata_only_and_sampled_fails_closed(tmp_path: Path):
    manifest = _dataset(tmp_path)
    target = manifest.parent / "calendars" / "day.txt"
    target.write_text("tampered\n", encoding="utf-8")

    verify_dataset_manifest(manifest, mode="manifest")
    with pytest.raises(ValueError, match="checksum mismatch"):
        verify_dataset_manifest(manifest, mode="sampled")


def test_tampered_receipt_is_rejected(tmp_path: Path):
    manifest = _dataset(tmp_path)
    receipts = tmp_path / "receipts"
    evidence: dict[str, object] = {}
    verify_dataset_manifest(manifest, receipt_dir=receipts, evidence=evidence)
    receipt = Path(str(evidence["receipt"]))
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload["fileCount"] = 999
    receipt.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="receipt checksum mismatch"):
        verify_dataset_manifest(manifest, receipt_dir=receipts, reuse_receipt=True)


def test_release_resolve_is_fast_but_explicit_deep_detects_corruption(tmp_path: Path):
    release = LocalReleasePublisher(tmp_path / "releases").import_qlib(_provider(tmp_path / "legacy"))
    item = release.manifest["components"][0]["files"][0]
    target = release.manifest_path.parent / str(item["path"]).removeprefix("components/qlib_dataset/")
    target = release.manifest_path.parent / str(item["path"])
    target.write_bytes(b"tampered")
    store = FileReleaseStore(tmp_path / "releases")

    assert store.resolve(release.data_release_id).data_release_id == release.data_release_id
    with pytest.raises(ValueError, match="checksum mismatch|size mismatch"):
        store.resolve(release.data_release_id, mode="deep")
