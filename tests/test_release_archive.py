from __future__ import annotations

import sqlite3
from pathlib import Path

from qlib_platform.bootstrap import _archive_standalone_history
from qlib_platform.releases import FileReleaseStore, import_qlib_dataset, release_store_root
from qlib_platform.settings import Paths, Settings


def _settings(tmp_path: Path) -> Settings:
    paths = Paths.from_root(tmp_path / "data")
    paths.mkdirs()
    return Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={
            "mode": "standalone",
            "release_store": {"active_keep": 1},
            "qlib": {"dataset_name": "cn_standalone", "dataset_ref": "standalone-current"},
        },
        paths=paths,
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=paths.root / "qlib" / "current",
    )


def _provider(root: Path, dates: list[str], payload: bytes) -> Path:
    (root / "calendars").mkdir(parents=True)
    (root / "instruments").mkdir()
    (root / "features" / "sh600000").mkdir(parents=True)
    (root / "calendars" / "day.txt").write_text("\n".join(dates) + "\n", encoding="utf-8")
    (root / "instruments" / "all.txt").write_text(
        f"SH600000\t{dates[0]}\t{dates[-1]}\n", encoding="utf-8"
    )
    (root / "features" / "sh600000" / "close.day.bin").write_bytes(payload)
    return root


def test_standalone_archive_updates_registry_manifest_path(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    older, _ = import_qlib_dataset(
        settings,
        _provider(tmp_path / "provider-old", ["2026-09-01"], b"old"),
    )
    latest, _ = import_qlib_dataset(
        settings,
        _provider(tmp_path / "provider-new", ["2026-09-01", "2026-09-02"], b"new"),
    )

    archived = _archive_standalone_history(settings, latest.data_release_id)

    store_root = release_store_root(settings)
    archived_manifest = store_root / "archive" / older.data_release_id / "manifest.json"
    assert archived == 1
    assert archived_manifest.is_file()
    assert not (store_root / older.data_release_id).exists()
    assert (store_root / latest.data_release_id / "manifest.json").is_file()

    with sqlite3.connect(settings.registry_path) as connection:
        row = connection.execute(
            "SELECT manifest_path FROM data_releases WHERE data_release_id=?",
            (older.data_release_id,),
        ).fetchone()
    assert row is not None
    assert Path(str(row[0])).resolve() == archived_manifest.resolve()

    replay = FileReleaseStore(store_root).resolve(older.data_release_id, mode="manifest")
    assert replay.data_release_id == older.data_release_id
