import pytest

from qlib_platform.symbols import qlib_to_ts, ts_to_qlib


def test_symbol_round_trip():
    for ts_code, symbol in [("600000.SH", "SH600000"), ("000001.SZ", "SZ000001"), ("830799.BJ", "BJ830799")]:
        assert ts_to_qlib(ts_code) == symbol
        assert qlib_to_ts(symbol) == ts_code


@pytest.mark.parametrize(
    ("ts_code", "symbol"),
    [
        ("T600018.SH", "SH600018"),
        ("ST600018.SH", "SH600018"),
        ("N000001.SZ", "SZ000001"),
        ("C830799.BJ", "BJ830799"),
    ],
)
def test_prefixed_a_share_code_normalizes_to_six_digit_symbol(ts_code, symbol):
    assert ts_to_qlib(ts_code) == symbol


def test_invalid_symbol():
    with pytest.raises(ValueError):
        ts_to_qlib("AAPL")
