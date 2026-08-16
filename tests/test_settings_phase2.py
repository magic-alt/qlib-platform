from __future__ import annotations

from pathlib import Path

import pytest

from tushare_qlib.settings import Settings


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


def test_settings_rejects_extends_cycle(tmp_path: Path):
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    first.write_text("extends: second.yaml\n", encoding="utf-8")
    second.write_text("extends: first.yaml\n", encoding="utf-8")

    with pytest.raises(ValueError, match="extends cycle"):
        Settings.load(first, create_dirs=False)
