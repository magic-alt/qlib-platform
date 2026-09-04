from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import qlib_platform.dataset_manifest as dataset_manifest_module
from qlib_platform.dataset_manifest import verify_dataset_manifest, write_dataset_manifest
from qlib_platform.releases import FileReleaseStore, LocalReleasePublisher
from qlib_platform.verification import deterministic_sample


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


def _dense_dataset(tmp_path: Path, *, count: int = 128) -> Path:
    root = tmp_path / "dense-dataset"
    feature_root = root / "features" / "sh600000"
    feature_root.mkdir(parents=True)
    for index in range(count):
        (feature_root / f"feature_{index:03d}.day.bin").write_bytes(f"value-{index}".encode())
    manifest, _ = write_dataset_manifest(
        root,
        dataset_name="dense-test",
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
    assert evidence["hashedFileCount"] == 2
    assert Path(str(evidence["receipt"])).is_file()

    reused: dict[str, object] = {}
    verify_dataset_manifest(
        manifest,
        mode="deep",
        receipt_dir=receipts,
        reuse_receipt=True,
        evidence=reused,
    )
    assert reused["verificationSource"] == "receipt+inventory+sampled"
    assert reused["verifiedFileCount"] == 2
    assert reused["hashedFileCount"] == 2


def test_deep_receipt_reuse_hashes_only_the_guard_sample(tmp_path: Path, monkeypatch):
    manifest = _dataset(tmp_path)
    receipts = tmp_path / "receipts"
    verify_dataset_manifest(manifest, mode="deep", receipt_dir=receipts)

    original = dataset_manifest_module.sha256_file
    hashed: list[Path] = []

    def counting_sha256(path: str | Path) -> str:
        hashed.append(Path(path))
        return original(path)

    monkeypatch.setattr(dataset_manifest_module, "sha256_file", counting_sha256)
    evidence: dict[str, object] = {}
    verify_dataset_manifest(
        manifest,
        mode="deep",
        receipt_dir=receipts,
        reuse_receipt=True,
        sample_size=1,
        evidence=evidence,
    )

    assert evidence["verificationSource"] == "receipt+inventory+sampled"
    assert evidence["verifiedFileCount"] == 2
    assert evidence["hashedFileCount"] == 1
    assert len(hashed) == 1


def test_collocated_manifest_build_proof_avoids_rehashing_every_partition(tmp_path: Path, monkeypatch):
    manifest = _dataset(tmp_path)
    original = dataset_manifest_module.sha256_file
    hashed: list[Path] = []

    def counting_sha256(path: str | Path) -> str:
        hashed.append(Path(path))
        return original(path)

    monkeypatch.setattr(dataset_manifest_module, "sha256_file", counting_sha256)
    evidence: dict[str, object] = {}
    verify_dataset_manifest(
        manifest,
        mode="deep",
        reuse_receipt=True,
        sample_size=1,
        evidence=evidence,
    )

    assert evidence["verificationSource"] == "manifest-build+inventory+sampled"
    assert evidence["verifiedFileCount"] == 2
    assert evidence["hashedFileCount"] == 1
    assert len(hashed) == 1


def test_reused_deep_inventory_resolves_unique_directories_not_every_file(tmp_path: Path, monkeypatch):
    manifest = _dense_dataset(tmp_path)
    original_resolve = Path.resolve
    resolved: list[Path] = []

    def counting_resolve(path: Path, *args, **kwargs):
        resolved.append(path)
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", counting_resolve)
    evidence: dict[str, object] = {}
    verify_dataset_manifest(
        manifest,
        mode="deep",
        reuse_receipt=True,
        sample_size=1,
        workers=4,
        evidence=evidence,
    )

    assert evidence["verificationSource"] == "manifest-build+inventory+sampled"
    assert evidence["verifiedFileCount"] == 128
    assert evidence["hashedFileCount"] == 1
    assert evidence["inventoryDirectoryCount"] == 2
    assert len(resolved) < 10


def test_manifest_build_proof_falls_back_to_full_deep_after_mutation(tmp_path: Path):
    manifest = _dataset(tmp_path)
    target = manifest.parent / "features" / "sh600000" / "close.day.bin"
    target.write_bytes(b"wrong")
    future = manifest.stat().st_mtime_ns + 2_000_000_000
    os.utime(target, ns=(future, future))

    with pytest.raises(ValueError, match="checksum mismatch"):
        verify_dataset_manifest(manifest, mode="deep", reuse_receipt=True, sample_size=1)


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


def test_reused_receipt_does_not_hide_missing_payload(tmp_path: Path):
    manifest = _dataset(tmp_path)
    receipts = tmp_path / "receipts"
    verify_dataset_manifest(manifest, receipt_dir=receipts)
    (manifest.parent / "calendars" / "day.txt").unlink()

    with pytest.raises(ValueError, match="checksum mismatch"):
        verify_dataset_manifest(manifest, receipt_dir=receipts, reuse_receipt=True)


def test_reused_receipt_falls_back_to_deep_after_same_size_mutation(tmp_path: Path):
    manifest = _dataset(tmp_path)
    receipts = tmp_path / "receipts"
    evidence: dict[str, object] = {}
    verify_dataset_manifest(manifest, receipt_dir=receipts, evidence=evidence)
    receipt = Path(str(evidence["receipt"]))
    target = manifest.parent / "features" / "sh600000" / "close.day.bin"
    target.write_bytes(b"wrong")
    future = receipt.stat().st_mtime_ns + 2_000_000_000
    os.utime(target, ns=(future, future))

    with pytest.raises(ValueError, match="checksum mismatch"):
        verify_dataset_manifest(
            manifest,
            receipt_dir=receipts,
            reuse_receipt=True,
            sample_size=1,
        )


def test_deterministic_sample_is_bounded_and_rejects_invalid_size():
    entries = [{"path": f"features/symbol_{index}/close.bin"} for index in range(200)]

    first = deterministic_sample(entries, identity="dataset", path_key="path", sample_size=16)
    second = deterministic_sample(reversed(entries), identity="dataset", path_key="path", sample_size=16)

    assert len(first) == 16
    assert first == second
    with pytest.raises(ValueError, match="sample size must be positive"):
        deterministic_sample(entries, identity="dataset", path_key="path", sample_size=0)


def test_release_resolve_is_fail_closed_but_manifest_mode_is_explicit(tmp_path: Path):
    release = LocalReleasePublisher(tmp_path / "releases").import_qlib(_provider(tmp_path / "legacy"))
    item = release.manifest["components"][0]["files"][0]
    target = release.manifest_path.parent / str(item["path"])
    target.write_bytes(b"tampered")
    store = FileReleaseStore(tmp_path / "releases")

    assert store.resolve(release.data_release_id, mode="manifest").data_release_id == release.data_release_id
    with pytest.raises(ValueError, match="checksum mismatch|size mismatch"):
        store.resolve(release.data_release_id)
