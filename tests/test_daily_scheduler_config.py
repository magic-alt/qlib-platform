from __future__ import annotations

from pathlib import Path

from qlib_platform.runtime.scheduler import render


def test_systemd_daily_run_uses_canonical_schedule(tmp_path: Path):
    python_exe = tmp_path / "python"
    python_exe.write_text("", encoding="utf-8")
    config = tmp_path / "pipeline.yaml"
    config.write_text("project_root: ./data\nqlib: {}\n", encoding="utf-8")
    output = tmp_path / "systemd"

    paths = render(
        "systemd",
        tmp_path,
        python_exe,
        config,
        output,
        schedule_time="19:05",
        schedule_timezone="Asia/Shanghai",
    )

    rendered = {path.name: path.read_text(encoding="utf-8") for path in paths}
    assert "qlib_platform.runtime.production_daily_run" in rendered["qlib-platform-daily-sync.service"]
    assert "19:05:00 Asia/Shanghai" in rendered["qlib-platform-daily-sync.timer"]
    assert "18:30" not in rendered["qlib-platform-daily-sync.timer"]


def test_launchd_daily_run_uses_same_canonical_clock(tmp_path: Path):
    python_exe = tmp_path / "python"
    python_exe.write_text("", encoding="utf-8")
    config = tmp_path / "pipeline.yaml"
    config.write_text("project_root: ./data\nqlib: {}\n", encoding="utf-8")
    output = tmp_path / "launchd"

    [path] = render(
        "launchd",
        tmp_path,
        python_exe,
        config,
        output,
        schedule_time="19:05",
        schedule_timezone="Asia/Shanghai",
    )
    content = path.read_text(encoding="utf-8")

    assert "qlib_platform.runtime.production_daily_run" in content
    assert "<key>Hour</key><integer>19</integer>" in content
    assert "<key>Minute</key><integer>5</integer>" in content
    assert "<integer>18</integer>" not in content
