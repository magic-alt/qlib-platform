import pytest

from tushare_qlib.symbols import qlib_to_ts, ts_to_qlib


def test_symbol_round_trip():
    for ts_code, symbol in [("600000.SH", "SH600000"), ("000001.SZ", "SZ000001"), ("830799.BJ", "BJ830799")]:
        assert ts_to_qlib(ts_code) == symbol
        assert qlib_to_ts(symbol) == ts_code


def test_invalid_symbol():
    with pytest.raises(ValueError):
        ts_to_qlib("AAPL")
