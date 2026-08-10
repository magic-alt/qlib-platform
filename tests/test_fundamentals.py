import pandas as pd

from tushare_qlib.fundamentals import build_pit_fundamentals


def test_pit_fundamentals_do_not_leak_before_announcement():
    reports = pd.DataFrame({"ts_code": ["000001.SZ"], "end_date": ["2025-12-31"], "ann_date": ["2026-03-30"],
                            "roe_waa_pit": [0.1], "roa_pit": [0.05], "netprofit_margin_pit": [0.1],
                            "netprofit_yoy_pit": [0.2], "or_yoy_pit": [0.1], "debt_to_assets_pit": [0.4], "ocf_to_or_pit": [0.2]})
    calendar = pd.DataFrame({"cal_date": ["2026-03-27", "2026-03-30", "2026-03-31"], "is_open": [1, 1, 1]})
    result = build_pit_fundamentals(reports, calendar)
    assert result["trade_date"].dt.strftime("%Y-%m-%d").tolist() == ["2026-03-30", "2026-03-31"]
