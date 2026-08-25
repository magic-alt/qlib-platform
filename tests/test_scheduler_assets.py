from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).parents[1]


def _module():
    spec = importlib.util.spec_from_file_location(
        "render_standalone_scheduler", ROOT / "scripts" / "render_standalone_scheduler.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scheduler_templates_render_standalone_config(tmp_path: Path):
    module = _module()
    python_exe = tmp_path / "python"
    python_exe.write_text("", encoding="utf-8")
    config = ROOT / "configs" / "pipeline.standalone.yaml"

    systemd = module.render("systemd", ROOT, python_exe, config, tmp_path / "systemd")
    launchd = module.render("launchd", ROOT, python_exe, config, tmp_path / "launchd")

    assert len(systemd) == 2
    assert len(launchd) == 1
    for path in [*systemd, *launchd]:
        content = path.read_text(encoding="utf-8")
        assert "pipeline.standalone.yaml" in content or path.suffix == ".timer"
        assert "@REPO_ROOT@" not in content
