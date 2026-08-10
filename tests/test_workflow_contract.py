import builtins
from pathlib import Path

import pytest

from tushare_qlib.settings import Paths, Settings
from tushare_qlib.workflow_contract import validate_qrun_contract


def test_qrun_contract_is_configuration_only_and_discloses_uncovered_limit_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    settings = Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={
            "strategy": {
                "topk_dropout": {
                    "topk": 40,
                    "n_drop": 3,
                    "hold_thresh": 10,
                    "only_tradable": True,
                    "forbid_all_trade_at_limit": True,
                    "risk_degree": 0.87,
                }
            },
            "research": {
                "benchmark": "SH000300",
                "deal_price": "open",
                "trade_unit": 100,
                "open_cost": 0.00035,
                "close_cost": 0.00085,
                "min_cost": 5,
                "max_participation_rate": 0.05,
            },
        },
        paths=Paths.from_root(tmp_path / "data"),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib-data",
    )
    workflow = tmp_path / "workflow.yaml"
    workflow.write_text(
        """
task:
  record:
    - class: PortAnaRecord
      kwargs:
        config:
          strategy:
            kwargs:
              topk: 40
              n_drop: 3
              hold_thresh: 10
              only_tradable: true
              forbid_all_trade_at_limit: true
              risk_degree: 0.87
          backtest:
            benchmark: SH000300
            exchange_kwargs:
              limit_threshold: 0.095
              deal_price: open
              volume_threshold: [current, "$volume * 0.05"]
              trade_unit: 100
              open_cost: 0.00035
              close_cost: 0.00085
              min_cost: 5
""",
        encoding="utf-8",
    )

    real_import = builtins.__import__

    def reject_runtime_imports(name: str, *args, **kwargs):
        if name == "qlib" or name.startswith("qlib.") or name == "lightgbm":
            raise AssertionError(f"runtime import is forbidden during config validation: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_runtime_imports)
    result = validate_qrun_contract(settings, workflow)

    assert result["passed"] is True
    assert result["certifiedExecutionEquivalent"] is False
    assert result["uncoveredSemantics"]["limit_threshold"]["qrun"] == 0.095
