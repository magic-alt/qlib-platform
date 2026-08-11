import builtins
from pathlib import Path

import pytest

from tushare_qlib.settings import Paths, Settings
from tushare_qlib.workflow_contract import validate_qrun_contract


def _settings(tmp_path: Path) -> Settings:
    return Settings(
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
            "universe": {
                "instruments": "all",
                "min_listed_days": 120,
                "min_circ_mv_yuan": 2_000_000_000,
                "min_money_20d_yuan": 20_000_000,
                "exclude_st": True,
                "allow_unknown_st": False,
            },
        },
        paths=Paths.from_root(tmp_path / "data"),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib-data",
    )


def _write_workflow(tmp_path: Path, limit: str = '["$is_limit_up > 0", "$is_limit_down > 0"]') -> Path:
    workflow = tmp_path / "workflow.yaml"
    workflow.write_text(
        f"""
task:
  dataset:
    kwargs:
      handler:
        kwargs:
          instruments: all
          shared_processors:
            - class: AshareUniverseFilter
              kwargs:
                min_listed_days: 120
                min_circ_mv_yuan: 2000000000
                min_money_20d_yuan: 20000000
                exclude_st: true
                allow_unknown_st: false
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
              limit_threshold: {limit}
              deal_price: open
              volume_threshold: [current, "$volume * 0.05"]
              trade_unit: 100
              open_cost: 0.00035
              close_cost: 0.00085
              min_cost: 5
""",
        encoding="utf-8",
    )
    return workflow


def test_qrun_contract_certifies_equivalent_static_semantics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    real_import = builtins.__import__

    def reject_runtime_imports(name: str, *args, **kwargs):
        if name == "qlib" or name.startswith("qlib.") or name == "lightgbm":
            raise AssertionError(f"runtime import is forbidden during config validation: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_runtime_imports)
    result = validate_qrun_contract(_settings(tmp_path), _write_workflow(tmp_path))

    assert result["passed"] is True
    assert result["certifiedExecutionEquivalent"] is True
    assert result["certificationStatus"] == "certified"
    assert result["mismatches"] == {}


@pytest.mark.parametrize(
    ("limit", "actual"),
    [
        ("0.095", 0.095),
        (
            '["$close == $up_limit", "$close == $down_limit"]',
            ("$close == $up_limit", "$close == $down_limit"),
        ),
    ],
)
def test_qrun_contract_reports_scalar_and_tuple_limit_mismatches(tmp_path: Path, limit: str, actual: object):
    result = validate_qrun_contract(_settings(tmp_path), _write_workflow(tmp_path, limit))

    assert result["passed"] is False
    assert result["certifiedExecutionEquivalent"] is False
    assert result["certificationStatus"] == "non-certified"
    mismatch = result["mismatches"]["execution.limit_threshold"]
    assert mismatch["qrun"] == actual
    assert result["uncoveredSemantics"]["limit_threshold"]["qrun"] == actual


def test_qrun_contract_reports_volume_participation_universe_and_benchmark(tmp_path: Path):
    workflow = _write_workflow(tmp_path)
    content = workflow.read_text(encoding="utf-8")
    content = content.replace("instruments: all", "instruments: csi500")
    content = content.replace("min_listed_days: 120", "min_listed_days: 60")
    content = content.replace("benchmark: SH000300", "benchmark: SH000905")
    content = content.replace('[current, "$volume * 0.05"]', '[all, "$volume * 0.10"]')
    workflow.write_text(content, encoding="utf-8")

    result = validate_qrun_contract(_settings(tmp_path), workflow)

    assert result["passed"] is False
    assert "execution.volume_threshold.mode" in result["mismatches"]
    assert "execution.max_participation_rate" in result["mismatches"]
    assert "universe.instruments" in result["mismatches"]
    assert "universe.min_listed_days" in result["mismatches"]
    assert "benchmark" in result["mismatches"]
