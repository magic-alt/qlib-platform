from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from tushare_qlib.project_audit import _source_files


def test_source_inventory_never_opens_env_files(tmp_path: Path, monkeypatch):
    env = tmp_path / ".env"
    source = tmp_path / "module.py"
    env.write_text("placeholder", encoding="utf-8")
    source.write_text("print('safe')", encoding="utf-8")
    monkeypatch.setattr(
        "tushare_qlib.project_audit.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=b".env\0module.py\0",
        ),
    )

    assert _source_files(tmp_path) == [source]
