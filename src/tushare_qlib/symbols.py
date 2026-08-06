from __future__ import annotations

import re

_TS_PATTERN = re.compile(r"^(?P<code>\d{6})\.(?P<exchange>SH|SZ|BJ)$", re.IGNORECASE)
_QLIB_PATTERN = re.compile(r"^(?P<exchange>SH|SZ|BJ)(?P<code>\d{6})$", re.IGNORECASE)


def ts_to_qlib(ts_code: str) -> str:
    match = _TS_PATTERN.fullmatch(ts_code.strip())
    if not match:
        raise ValueError(f"Unsupported Tushare A-share code: {ts_code!r}")
    return f"{match.group('exchange').upper()}{match.group('code')}"


def qlib_to_ts(symbol: str) -> str:
    match = _QLIB_PATTERN.fullmatch(symbol.strip())
    if not match:
        raise ValueError(f"Unsupported Qlib A-share symbol: {symbol!r}")
    return f"{match.group('code')}.{match.group('exchange').upper()}"
