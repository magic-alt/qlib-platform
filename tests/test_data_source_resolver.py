from __future__ import annotations

from pathlib import Path

from tushare_qlib.data_source_resolver import resolve_source
from tushare_qlib.settings import Settings


def _settings(tmp_path: Path) -> Settings:
    config = tmp_path / "pipeline.yaml"
    config.write_text(
        "\n".join(
            [
                "mode: standalone",
                f"project_root: {tmp_path / 'data'}",
                "data_source: {kind: auto}",
                "storage: {registry_path: ''}",
                "release_store: {kind: file, root: ''}",
                "qlib: {dataset_dir: '', versions_root: '', dataset_ref: research-current}",
            ]
        ),
        encoding="utf-8",
    )
    return Settings.load(config)


def test_resolver_reports_data_unavailable_without_failing_startup(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setattr("tushare_qlib.settings.load_dotenv", lambda: None)
    settings = _settings(tmp_path)

    result = resolve_source(settings)

    assert result.status == "DATA_UNAVAILABLE"
    assert result.source is None


def test_resolver_discovers_existing_qlib_provider(tmp_path: Path):
    settings = _settings(tmp_path)
    (settings.qlib_data_uri / "calendars").mkdir(parents=True)
    (settings.qlib_data_uri / "instruments").mkdir()
    (settings.qlib_data_uri / "features").mkdir()
    (settings.qlib_data_uri / "calendars" / "day.txt").write_text("2026-08-24\n", encoding="utf-8")

    result = resolve_source(settings)

    assert result.status == "IMPORT_REQUIRED"
    assert result.source == "qlib"
    assert result.path == settings.qlib_data_uri


def test_resolver_prefers_local_raw_before_download(tmp_path: Path):
    settings = _settings(tmp_path)
    (settings.paths.raw / "daily").mkdir(parents=True)

    result = resolve_source(settings)

    assert result.status == "BUILD_REQUIRED"
    assert result.source == "local_raw"
