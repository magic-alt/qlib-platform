from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_standalone_environment_template_is_copy_ready_without_secrets():
    active = [
        line.strip()
        for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert "QLIB_DATA_ROOT=./data" in active
    assert "TUSHARE_CALLS_PER_MINUTE=180" in active
    assert "TUSHARE_TOKEN=" in active
    assert "QLIB_REPO=" in active
    assert "QLIB_DATA_URI=" in active
    assert not any(line.startswith("QUANT_DATA_ROOT=") for line in active)
    assert not any(line.startswith("DATASET_RELEASE_ID=") for line in active)


def test_windows_daily_task_defaults_to_standalone_profile():
    script = (ROOT / "scripts" / "register_tushare_daily_sync_task.ps1").read_text(encoding="utf-8")

    assert '[string]$ConfigPath = "configs\\pipeline.standalone.yaml"' in script
