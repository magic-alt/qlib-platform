from __future__ import annotations

from qlib_platform.data.sources.mysql import (
    MysqlClient,
    _coerce_params,
    build_connection_kwargs,
    build_lean_canonical_range_endpoints,
    build_mysql_endpoints,
)


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
    assert "b.trade_date=%(trade_date_iso)s" in defs["daily"]["query"]
    assert "universe_membership" in defs["daily"]["query"]
    assert "asset_class='index'" not in defs["daily"]["query"]
    assert "total_share_shares" in defs["daily_basic"]["query"]
    assert "t.source LIKE CONCAT(%(source)s,':%%')" in defs["stk_limit"]["query"]
    assert defs["moneyflow"]["enabled"] is False


def test_canonical_params_include_index_friendly_iso_dates():
    params = _coerce_params({"trade_date": "20250102", "start_date": "20250101", "end_date": "2025-01-31"})

    assert params["trade_date"] == "20250102"
    assert params["trade_date_iso"] == "2025-01-02"
    assert params["start_date_iso"] == "2025-01-01"
    assert params["end_date_iso"] == "2025-01-31"


def test_canonical_range_queries_replace_per_day_predicates():
    definitions = build_lean_canonical_range_endpoints(
        {"schema": "lean_canonical_v1"}, optional_endpoints={"moneyflow": False}
    )

    for name in ("daily", "adj_factor", "daily_basic", "stk_limit", "suspend_d", "stock_st"):
        query = definitions[name]["query"]
        assert "%(trade_date_iso)s" not in query
        assert "%(start_date_iso)s" in query
        assert "%(end_date_iso)s" in query


def test_mysql_client_accepts_read_only_select_and_rejects_dml(monkeypatch):
    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, query, params):
            self.query = query
            self.params = params

        def fetchall(self):
            return [{"marker": 1}]

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def cursor(self):
            return FakeCursor()

    client = MysqlClient(connection={}, endpoint_queries={"probe": "  SELECT 1 AS marker"})
    monkeypatch.setattr(client, "_connect", lambda: FakeConnection())

    result = client.fetch("probe")

    assert result.status == "success"
    assert result.data.to_dict("records") == [{"marker": 1}]

    client.endpoint_queries["probe"] = "DELETE FROM market_daily_bars"
    try:
        client.fetch("probe")
    except ValueError as exc:
        assert "not a read-only SQL statement" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("DML query was accepted")
