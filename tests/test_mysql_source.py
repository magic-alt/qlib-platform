from __future__ import annotations

from tushare_qlib.mysql_source import build_connection_kwargs, build_mysql_endpoints


def test_build_mysql_endpoints_default_and_override():
    cfg = {
        "endpoints": {
            "moneyflow": {
                "enabled": False,
                "query": "SELECT 1 AS marker",
            }
        },
        "tables": {
            "daily": "daily_cn",
        },
    }
    defs = build_mysql_endpoints(cfg, optional_endpoints={"moneyflow": True, "stk_limit": False})

    assert (
        defs["daily"]["query"]
        == "SELECT ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount "
        "FROM daily_cn WHERE trade_date=%(trade_date)s ORDER BY trade_date,ts_code"
    )
    assert defs["moneyflow"]["enabled"] is False
    assert defs["moneyflow"]["query"] == "SELECT 1 AS marker"
    assert defs["stk_limit"]["enabled"] is False


def test_build_connection_kwargs_env_fallback(monkeypatch):
    for key in [
        "LEAN_MYSQL_HOST",
        "LEAN_MYSQL_USER",
        "LEAN_MYSQL_PASSWORD",
        "LEAN_MYSQL_DB",
        "LEAN_MYSQL_PORT",
    ]:
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("LEAN_MYSQL_HOST", "127.0.0.1")
    monkeypatch.setenv("LEAN_MYSQL_USER", "tester")
    monkeypatch.setenv("LEAN_MYSQL_PASSWORD", "secret")
    monkeypatch.setenv("LEAN_MYSQL_DB", "market")
    monkeypatch.setenv("LEAN_MYSQL_PORT", "3307")

    kwargs = build_connection_kwargs(
        {"host": "", "user": "", "password": "", "database": "", "readonly": True}
    )

    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["user"] == "tester"
    assert kwargs["password"] == "secret"
    assert kwargs["database"] == "market"
    assert kwargs["port"] == 3307


def test_build_lean_canonical_endpoints_use_pit_and_unit_projection():
    defs = build_mysql_endpoints(
        {"schema": "lean_canonical_v1", "source": "tushare", "universe": "CSI300"},
        optional_endpoints={"moneyflow": False},
    )

    assert "FROM ashare_daily_bars b" in defs["daily"]["query"]
    assert "b.volume/100.0 vol" in defs["daily"]["query"]
    assert "universe_membership" in defs["daily"]["query"]
    assert "asset_class='index'" not in defs["daily"]["query"]
    assert defs["moneyflow"]["enabled"] is False
