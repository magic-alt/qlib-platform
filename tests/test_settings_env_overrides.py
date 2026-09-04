from __future__ import annotations

from pathlib import Path

from qlib_platform.settings import Settings


def _write_config(path: Path, *, opt_in: bool) -> Path:
    qlib_lines = [
        "qlib:",
        "  repo_path: ''",
        "  dataset_dir: ''",
        "  versions_root: ''",
        "  dataset_name: test",
        "  dataset_ref: test-current",
    ]
    if opt_in:
        qlib_lines.insert(2, "  repo_path_env: QLIB_REPO")
        qlib_lines.insert(4, "  dataset_dir_env: QLIB_DATA_URI")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "mode: standalone",
                "project_root: ./data",
                "data_source: {kind: auto}",
                *qlib_lines,
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_custom_config_ignores_unscoped_process_qlib_env(tmp_path: Path, monkeypatch) -> None:
    external_repo = tmp_path / "external-qlib"
    external_data = tmp_path / "external-provider"
    monkeypatch.setenv("QLIB_REPO", str(external_repo))
    monkeypatch.setenv("QLIB_DATA_URI", str(external_data))

    settings = Settings.load(_write_config(tmp_path / "configs" / "custom.yaml", opt_in=False))

    assert settings.qlib_repo is None
    assert settings.qlib_data_uri == (tmp_path / "data" / "qlib" / "current").resolve()


def test_profile_can_opt_into_qlib_env_overrides(tmp_path: Path, monkeypatch) -> None:
    external_repo = tmp_path / "external-qlib"
    external_data = tmp_path / "external-provider"
    monkeypatch.setenv("QLIB_REPO", str(external_repo))
    monkeypatch.setenv("QLIB_DATA_URI", str(external_data))

    settings = Settings.load(_write_config(tmp_path / "configs" / "standalone.yaml", opt_in=True))

    assert settings.qlib_repo == external_repo.resolve()
    assert settings.qlib_data_uri == external_data.resolve()
