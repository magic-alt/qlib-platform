from __future__ import annotations

from pathlib import Path

import pytest

from qlib_platform.settings import Settings


def test_settings_extends_base_and_appends_qlib_fields(tmp_path: Path):
    base = tmp_path / "base.yaml"
    base.write_text(
        "\n".join(
            [
                f"project_root: {tmp_path / 'data'}",
                "qlib:",
                f"  dataset_dir: {tmp_path / 'qlib'}",
                "  include_fields: [close, volume]",
                "experiment:",
                "  label: {spec: return_5d_t1_v1}",
                "  alpha: {pack: alpha158_pit_v1}",
            ]
        ),
        encoding="utf-8",
    )
    child = tmp_path / "child.yaml"
    child.write_text(
        "\n".join(
            [
                "extends: base.yaml",
                "qlib:",
                "  include_fields_extra: [total_assets_pit, close]",
                "experiment:",
                "  alpha: {pack: ashare_alpha_phase2_v1, feature_set: A1}",
            ]
        ),
        encoding="utf-8",
    )

    settings = Settings.load(child, create_dirs=False)

    assert settings.data["experiment"]["label"]["spec"] == "return_5d_t1_v1"
    assert settings.data["experiment"]["alpha"]["feature_set"] == "A1"
    assert settings.qlib_include_fields == ("close", "volume", "total_assets_pit")


def test_settings_treats_omitted_extra_fields_as_empty():
    settings = Settings.__new__(Settings)
    object.__setattr__(settings, "data", {"qlib": {"include_fields": ["close"]}})

    assert settings.qlib_include_fields == ("close",)


def test_settings_rejects_extends_cycle(tmp_path: Path):
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    first.write_text("extends: second.yaml\n", encoding="utf-8")
    second.write_text("extends: first.yaml\n", encoding="utf-8")

    with pytest.raises(ValueError, match="extends cycle"):
        Settings.load(first, create_dirs=False)


def test_standalone_profile_loads_without_platform_or_tushare_environment(monkeypatch):
    for name in (
        "QUANT_DATA_ROOT",
        "DATASET_RELEASE_ID",
        "QLIB_REPO",
        "QLIB_DATA_URI",
        "QLIB_DATA_ROOT",
        "TUSHARE_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("qlib_platform.settings.load_dotenv", lambda: None)

    settings = Settings.load("configs/pipeline.standalone.yaml", create_dirs=False)

    assert settings.mode == "standalone"
    assert settings.source_kind == "auto"
    assert settings.uses_tushare_source() is True
    assert settings.tushare_token is None
    assert settings.paths.root.name == "data"
    assert settings.qlib_data_uri == settings.paths.root / "qlib" / "current"


def test_standalone_root_environment_is_optional_override(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("QLIB_DATA_ROOT", str(tmp_path / "standalone-root"))
    monkeypatch.setattr("qlib_platform.settings.load_dotenv", lambda: None)

    settings = Settings.load("configs/pipeline.standalone.yaml", create_dirs=False)

    assert settings.paths.root == (tmp_path / "standalone-root").resolve()


def test_generic_data_release_configuration_is_supported(tmp_path: Path):
    settings = Settings.__new__(Settings)
    object.__setattr__(
        settings,
        "data",
        {
            "mode": "integrated",
            "data_source": {
                "kind": "data_release",
                "data_release": {"id": "ds_" + "a" * 64, "data_root": str(tmp_path)},
            },
        },
    )

    assert settings.uses_data_release()
    assert settings.data_release_config["id"] == "ds_" + "a" * 64
