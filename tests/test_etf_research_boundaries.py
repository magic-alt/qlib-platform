from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest
from qlib.contrib.data.handler import Alpha158

from qlib_platform.data.etf_handler import AshareEtfCore, etf_shared_processors
from qlib_platform.research.reporting.etf_fixture import (
    render_etf_certification_report,
    write_etf_certification_report,
)


def test_etf_handler_removes_only_stock_universe_processor(monkeypatch: pytest.MonkeyPatch) -> None:
    processors = [
        {"class": "AshareUniverseFilter", "module_path": "qlib_platform.data.processors"},
        {"class": "KeepMe", "module_path": "fixture"},
    ]
    assert etf_shared_processors(processors) == [processors[1]]
    marker = object()
    assert etf_shared_processors(marker) is marker

    captured: dict[str, object] = {}

    def fake_alpha_init(self, *args, **kwargs) -> None:
        del self, args
        captured.update(kwargs)

    monkeypatch.setattr(Alpha158, "__init__", fake_alpha_init)
    AshareEtfCore(shared_processors=processors, instruments="all")

    assert captured["shared_processors"] == [processors[1]]
    assert captured["instruments"] == "all"


def _report_result() -> SimpleNamespace:
    return SimpleNamespace(
        train_rows=12,
        evaluation_rows=6,
        coefficients=(0.1, 0.2),
        daily_backtest=pd.DataFrame(
            {
                "gross_return": [0.01, -0.005, 0.02],
                "net_return": [0.009, -0.006, 0.019],
                "transaction_cost": [0.001, 0.001, 0.001],
            },
            index=pd.to_datetime(["2026-09-01", "2026-09-02", "2026-09-03"]),
        ),
        diagnostics=SimpleNamespace(summary=pd.DataFrame({"feature": ["RET_1", "MONEY_RATIO_20"]})),
    )


def test_etf_certification_report_is_deterministic_research_only_artifact(tmp_path) -> None:
    result = _report_result()
    first = render_etf_certification_report(result)
    second = render_etf_certification_report(result)

    assert first == second
    assert "A-share ETF Research Fixture Report" in first
    assert "ashare_etf_v1" in first
    assert "etf_core_v1" in first
    assert "equity_daily_basic" in first
    assert "one-session realized returns" in first
    assert "Execution authority: `none`" in first
    assert "Paper/Live" in first

    path = write_etf_certification_report(result, tmp_path / "etf" / "report.md")
    assert path.read_text(encoding="utf-8") == first


def test_etf_report_rejects_invalid_return_evidence() -> None:
    result = _report_result()
    result.daily_backtest.loc[:, "net_return"] = float("nan")
    with pytest.raises(ValueError, match="finite non-empty"):
        render_etf_certification_report(result)

    result = _report_result()
    result.daily_backtest.loc[result.daily_backtest.index[0], "net_return"] = -1.0
    with pytest.raises(ValueError, match="less than or equal to -100%"):
        render_etf_certification_report(result)
