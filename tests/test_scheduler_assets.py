from __future__ import annotations

from pathlib import Path

from tushare_qlib import scheduler


ROOT = Path(__file__).parents[1]


def test_scheduler_templates_render_standalone_config(tmp_path: Path):
    python_exe = tmp_path / "python"
    python_exe.write_text("", encoding="utf-8")
    config = ROOT / "configs" / "pipeline.standalone.yaml"

    systemd = scheduler.render("systemd", ROOT, python_exe, config, tmp_path / "systemd")
    launchd = scheduler.render("launchd", ROOT, python_exe, config, tmp_path / "launchd")

    assert len(systemd) == 2
    assert len(launchd) == 1
    for path in [*systemd, *launchd]:
        content = path.read_text(encoding="utf-8")
        assert "pipeline.standalone.yaml" in content or path.suffix == ".timer"
        assert "@REPO_ROOT@" not in content
    assert "Asia/Shanghai" in systemd[1].read_text(encoding="utf-8")
