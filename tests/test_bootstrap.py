from __future__ import annotations

from pathlib import Path

import pytest

from tushare_qlib.bootstrap import bootstrap
from tushare_qlib.settings import Paths, Settings


def _settings(tmp_path: Path, *, token: str | None = None) -> Settings:
    paths = Paths.from_root(tmp_path / "data")
    return Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={
            "mode": "standalone",
            "start_date": "20260101",
            "end_date": "20260824",
            "data_source": {"kind": "auto"},
            "qlib": {},
        },
        paths=paths,
        tushare_token=token,
        qlib_repo=None,
        qlib_data_uri=paths.root / "qlib" / "current",
    )


def test_tushare_bootstrap_requires_explicit_window_before_credentials(tmp_path: Path):
    with pytest.raises(ValueError, match="explicit --start and --end"):
        bootstrap(_settings(tmp_path), source="tushare")


def test_tushare_bootstrap_builds_all_required_release_inputs(tmp_path: Path, monkeypatch):
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        "tushare_qlib.bootstrap._run_cli",
        lambda _settings, *arguments: calls.append(tuple(arguments)),
    )

    result = bootstrap(
        _settings(tmp_path, token="dummy_test_token"),
        source="tushare",
        start="20260101",
        end="20260824",
    )

    assert result == {"status": "READY", "source": "tushare"}
    assert ("sync-dividends", "--bootstrap") in calls
    assert ("sync-industry", "--end", "20260824") in calls
    assert calls[-1] == ("dataset-build", "--start", "20260101", "--end", "20260824")
