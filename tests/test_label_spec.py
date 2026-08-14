from __future__ import annotations

from tushare_qlib.research_timing import LabelSpec


def test_label_spec_is_the_canonical_expression_for_non_default_signal_lag():
    spec = LabelSpec(horizon_days=5, signal_lag_days=2)

    assert spec.lookahead_days == 7
    assert spec.spec_id == "return_5d_t2_v1"
    assert spec.qlib_config() == (["Ref($close, -7)/Ref($close, -1) - 1"], ["LABEL0"])
    assert spec.to_manifest()["expression"] == spec.expression
