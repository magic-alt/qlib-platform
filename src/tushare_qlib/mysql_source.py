from __future__ import annotations

from datetime import date
import os
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse, unquote

import pandas as pd

from .client import FetchResult, RetryPolicy

MYSQL_DEFAULT_ENDPOINT_FIELDS: dict[str, str] = {
    "daily": "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount",
    "adj_factor": "ts_code,trade_date,adj_factor",
    "daily_basic": (
        "ts_code,trade_date,close,turnover_rate,turnover_rate_f,volume_ratio,pe,pe_ttm,pb,ps,ps_ttm,"
        "dv_ratio,dv_ttm,total_share,float_share,free_share,total_mv,circ_mv,limit_status"
    ),
    "moneyflow": (
        "ts_code,trade_date,buy_lg_vol,buy_lg_amount,sell_lg_vol,sell_lg_amount,"
        "buy_elg_vol,buy_elg_amount,sell_elg_vol,sell_elg_amount,net_mf_vol,net_mf_amount"
    ),
    "stk_limit": "ts_code,trade_date,pre_close,up_limit,down_limit",
    "suspend_d": "ts_code,trade_date,suspend_timing,suspend_type",
    "stock_st": "ts_code,name,trade_date,type,type_name",
    "stock_basic": (
        "ts_code,symbol,name,area,industry,market,exchange,list_status,list_date,delist_date,"
        "is_hs,act_name,act_ent_type"
    ),
    "trade_cal": "exchange,cal_date,is_open,pretrade_date",
}

MYSQL_DEFAULT_ENDPOINT_WHERE: dict[str, str] = {
    "daily": "trade_date=%(trade_date)s",
    "adj_factor": "trade_date=%(trade_date)s",
    "daily_basic": "trade_date=%(trade_date)s",
    "moneyflow": "trade_date=%(trade_date)s",
    "stk_limit": "trade_date=%(trade_date)s",
    "suspend_d": "trade_date=%(trade_date)s AND (%(suspend_type)s = '' OR suspend_type=%(suspend_type)s)",
    "stock_st": "trade_date=%(trade_date)s",
    "stock_basic": "(%(list_status)s = '' OR list_status=%(list_status)s) AND (%(exchange)s = '' OR exchange=%(exchange)s)",
    "trade_cal": "(%(exchange)s = '' OR exchange=%(exchange)s) AND cal_date BETWEEN %(start_date)s AND %(end_date)s",
}

MYSQL_DEFAULT_ENDPOINT_ORDER: dict[str, str] = {
    "daily": "trade_date,ts_code",
    "adj_factor": "trade_date,ts_code",
    "daily_basic": "trade_date,ts_code",
    "moneyflow": "trade_date,ts_code",
    "stk_limit": "trade_date,ts_code",
    "suspend_d": "trade_date,ts_code",
    "stock_st": "trade_date,ts_code",
    "stock_basic": "list_status,ts_code",
    "trade_cal": "exchange,cal_date",
}

MYSQL_DEFAULT_ENDPOINT_REQUIRED: dict[str, bool] = {
    "daily": True,
    "adj_factor": True,
    "daily_basic": True,
    "moneyflow": False,
    "stk_limit": False,
    "suspend_d": False,
    "stock_st": False,
    "stock_basic": True,
    "trade_cal": True,
}

MYSQL_METADATA_ENDPOINTS = {"stock_basic", "trade_cal"}

LEAN_CANONICAL_SCHEMA = "lean_canonical_v1"


def _symbol_sql(alias: str = "b") -> str:
    return (
        f"CONCAT({alias}.symbol,'.',CASE "
        f"WHEN LEFT({alias}.symbol,1) IN ('5','6','9') THEN 'SH' "
        f"WHEN LEFT({alias}.symbol,1) IN ('4','8') THEN 'BJ' ELSE 'SZ' END)"
    )


def _pit_filter(alias: str = "b") -> str:
    return (
        f"(%(universe)s='' OR EXISTS (SELECT 1 FROM universe_membership u "
        f"WHERE u.universe_code=%(universe)s AND u.symbol={alias}.symbol "
        f"AND u.start_date<={alias}.trade_date AND (u.end_date IS NULL OR u.end_date>={alias}.trade_date) "
        f"AND (u.announce_date IS NULL OR u.announce_date<={alias}.trade_date) "
        f"AND (u.effective_date IS NULL OR u.effective_date<={alias}.trade_date)))"
    )


def build_lean_canonical_endpoints(
    mysql_cfg: Mapping[str, Any],
    optional_endpoints: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return Tushare-compatible projections over Lean's canonical MySQL schema.

    Lean stores volume in shares and amount/market values in CNY.  The existing
    raw-to-curated pipeline consumes Tushare-native hands, thousands of CNY and
    ten-thousands of shares/CNY, so conversion is deliberately performed here.
    """
    symbol = _symbol_sql()
    pit = _pit_filter()
    date_eq = "REPLACE(b.trade_date,'-','')=%(trade_date)s"
    source = "(%(source)s='' OR b.source=%(source)s)"
    queries = {
        "daily": (
            f"SELECT {symbol} ts_code,REPLACE(b.trade_date,'-','') trade_date,b.open,b.high,b.low,b.close,"
            "b.prev_close pre_close,(b.close-b.prev_close) `change`,b.pct_change pct_chg,"
            "b.volume/100.0 vol,b.amount/1000.0 amount FROM ashare_daily_bars b "
            f"WHERE b.adjust='raw' AND {date_eq} AND {source} AND {pit} ORDER BY b.trade_date,b.symbol"
        ),
        "adj_factor": (
            f"SELECT {_symbol_sql('a')} ts_code,REPLACE(a.trade_date,'-','') trade_date,a.adj_factor "
            "FROM adjustment_factors a WHERE REPLACE(a.trade_date,'-','')=%(trade_date)s "
            "AND (%(source)s='' OR a.source=%(source)s) "
            f"AND {_pit_filter('a')} ORDER BY a.trade_date,a.symbol"
        ),
        "daily_basic": (
            f"SELECT {_symbol_sql('f')} ts_code,REPLACE(f.trade_date,'-','') trade_date,NULL close,"
            "MAX(CASE WHEN f.factor_name='turnover_rate' THEN f.value END) turnover_rate,"
            "MAX(CASE WHEN f.factor_name='turnover_rate_float' THEN f.value END) turnover_rate_f,"
            "MAX(CASE WHEN f.factor_name='volume_ratio' THEN f.value END) volume_ratio,"
            "MAX(CASE WHEN f.factor_name='pe' THEN f.value END) pe,"
            "MAX(CASE WHEN f.factor_name='pe_ttm' THEN f.value END) pe_ttm,"
            "MAX(CASE WHEN f.factor_name='pb' THEN f.value END) pb,"
            "MAX(CASE WHEN f.factor_name='ps' THEN f.value END) ps,"
            "MAX(CASE WHEN f.factor_name='ps_ttm' THEN f.value END) ps_ttm,"
            "MAX(CASE WHEN f.factor_name='dividend_yield' THEN f.value END) dv_ratio,"
            "MAX(CASE WHEN f.factor_name='dividend_yield_ttm' THEN f.value END) dv_ttm,"
            "MAX(CASE WHEN f.factor_name='total_shares' THEN f.value/10000.0 END) total_share,"
            "MAX(CASE WHEN f.factor_name='float_shares' THEN f.value/10000.0 END) float_share,"
            "MAX(CASE WHEN f.factor_name='free_float_shares' THEN f.value/10000.0 END) free_share,"
            "MAX(CASE WHEN f.factor_name='total_mv_cny' THEN f.value/10000.0 END) total_mv,"
            "MAX(CASE WHEN f.factor_name='circ_mv_cny' THEN f.value/10000.0 END) circ_mv,NULL limit_status "
            "FROM factor_values f WHERE REPLACE(f.trade_date,'-','')=%(trade_date)s "
            "AND (%(source)s='' OR f.source=CONCAT(%(source)s,':daily_basic') OR f.source=%(source)s) "
            f"AND {_pit_filter('f')} GROUP BY f.symbol,f.trade_date ORDER BY f.trade_date,f.symbol"
        ),
        "moneyflow": (
            "SELECT NULL ts_code,NULL trade_date,NULL buy_lg_vol,NULL buy_lg_amount,NULL sell_lg_vol,"
            "NULL sell_lg_amount,NULL buy_elg_vol,NULL buy_elg_amount,NULL sell_elg_vol,NULL sell_elg_amount,"
            "NULL net_mf_vol,NULL net_mf_amount WHERE 1=0"
        ),
        "stk_limit": (
            f"SELECT {_symbol_sql('t')} ts_code,REPLACE(t.trade_date,'-','') trade_date,NULL pre_close,"
            "t.limit_up up_limit,t.limit_down down_limit FROM ashare_trade_status t "
            "WHERE REPLACE(t.trade_date,'-','')=%(trade_date)s "
            "AND (%(source)s='' OR t.source=%(source)s) "
            f"AND {_pit_filter('t')} ORDER BY t.trade_date,t.symbol"
        ),
        "suspend_d": (
            f"SELECT {_symbol_sql('t')} ts_code,REPLACE(t.trade_date,'-','') trade_date,NULL suspend_timing,'S' suspend_type "
            "FROM ashare_trade_status t WHERE t.is_suspended=1 "
            "AND REPLACE(t.trade_date,'-','')=%(trade_date)s AND (%(source)s='' OR t.source=%(source)s) "
            f"AND {_pit_filter('t')} ORDER BY t.trade_date,t.symbol"
        ),
        "stock_st": (
            f"SELECT {_symbol_sql('t')} ts_code,s.name,REPLACE(t.trade_date,'-','') trade_date,'ST' type,'ST' type_name "
            "FROM ashare_trade_status t LEFT JOIN securities s ON s.symbol=t.symbol WHERE t.is_st=1 "
            "AND REPLACE(t.trade_date,'-','')=%(trade_date)s AND (%(source)s='' OR t.source=%(source)s) "
            f"AND {_pit_filter('t')} ORDER BY t.trade_date,t.symbol"
        ),
        "stock_basic": (
            f"SELECT {_symbol_sql('s')} ts_code,s.symbol,s.name,NULL area,NULL industry,NULL market,"
            "CASE WHEN LEFT(s.symbol,1) IN ('5','6','9') THEN 'SSE' WHEN LEFT(s.symbol,1) IN ('4','8') THEN 'BSE' ELSE 'SZSE' END exchange,"
            "CASE WHEN s.delisted_date IS NULL THEN 'L' ELSE 'D' END list_status,REPLACE(s.listed_date,'-','') list_date,"
            "REPLACE(s.delisted_date,'-','') delist_date,NULL is_hs,NULL act_name,NULL act_ent_type FROM securities s "
            "WHERE (%(list_status)s='' OR %(list_status)s=CASE WHEN s.delisted_date IS NULL THEN 'L' ELSE 'D' END) "
            "ORDER BY list_status,s.symbol"
        ),
        "trade_cal": (
            "SELECT 'SSE' exchange,REPLACE(c.trade_date,'-','') cal_date,c.is_open,"
            "REPLACE(c.prev_trade_date,'-','') pretrade_date FROM trade_calendar c "
            "WHERE c.market='china' AND REPLACE(c.trade_date,'-','') BETWEEN %(start_date)s AND %(end_date)s "
            "ORDER BY c.trade_date"
        ),
    }
    optional = optional_endpoints or {}
    result: dict[str, dict[str, Any]] = {}
    for name, query in queries.items():
        enabled = bool(optional.get(name, True)) if name not in MYSQL_METADATA_ENDPOINTS else True
        if name == "moneyflow":
            enabled = bool(optional.get(name, False))
        result[name] = {"query": query, "required": name in {"daily", "adj_factor", "daily_basic", "stock_basic", "trade_cal"}, "enabled": enabled}
    return result


def _coerce_param(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y%m%d") if not pd.isna(value) else value
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    if hasattr(value, "to_pydatetime"):
        try:
            return pd.Timestamp(value).strftime("%Y%m%d")
        except Exception:
            return value
    return value


def _coerce_params(params: Mapping[str, Any]) -> dict[str, Any]:
    return {key: _coerce_param(value) for key, value in params.items()}


def _read_mapping(config: Mapping[str, Any], key: str, default: Any = None) -> Any:
    if not isinstance(config, Mapping):
        return default
    value = config.get(key, default)
    if isinstance(value, str):
        value = value.strip()
        return value or default
    return value


def _parse_dsn(dsn: str) -> dict[str, Any]:
    parsed = urlparse(dsn)
    scheme = parsed.scheme.lower()
    if scheme not in {"mysql", "mysql+pymysql", "mysql+mysqlconnector"}:
        raise ValueError(f"Unsupported DSN scheme: {scheme}")
    query = parse_qs(parsed.query)
    params: dict[str, Any] = {}
    if parsed.hostname:
        params["host"] = parsed.hostname
    if parsed.port:
        params["port"] = parsed.port
    if parsed.username:
        params["user"] = unquote(parsed.username)
    if parsed.password:
        params["password"] = unquote(parsed.password)
    if parsed.path:
        database = parsed.path.lstrip("/")
        if database:
            params["database"] = database
    if "charset" in query and query["charset"]:
        params["charset"] = query["charset"][0]
    if "connect_timeout" in query and query["connect_timeout"]:
        params["connect_timeout"] = int(query["connect_timeout"][0])
    return params


def build_connection_kwargs(mysql_cfg: Mapping[str, Any]) -> dict[str, Any]:
    config = mysql_cfg or {}
    if not isinstance(config, Mapping):
        raise ValueError("data_source.mysql config must be a mapping")

    kwargs: dict[str, Any] = {}
    dsn = _read_mapping(config, "dsn")
    if not dsn:
        dsn = os.getenv("LEAN_MYSQL_DSN") or os.getenv("DATABASE_URL")
    if dsn:
        kwargs.update(_parse_dsn(dsn))

    env_map = {
        "host": "LEAN_MYSQL_HOST",
        "user": "LEAN_MYSQL_USER",
        "password": "LEAN_MYSQL_PASSWORD",
        "database": "LEAN_MYSQL_DB",
        "port": "LEAN_MYSQL_PORT",
        "charset": "LEAN_MYSQL_CHARSET",
    }

    explicit = {
        "host": _read_mapping(config, "host"),
        "port": _read_mapping(config, "port"),
        "user": _read_mapping(config, "user"),
        "password": _read_mapping(config, "password"),
        "database": _read_mapping(config, "database") or _read_mapping(config, "db"),
        "charset": _read_mapping(config, "charset"),
        "connect_timeout": _read_mapping(config, "connect_timeout", 10),
        "autocommit": _read_mapping(config, "autocommit", True),
    }
    for key, value in explicit.items():
        source = value
        if (source is None or source == "") and key in env_map:
            source = os.getenv(env_map[key], "")
        if source is None or source == "":
            continue
        if key == "port":
            kwargs[key] = int(source)
        else:
            kwargs[key] = source

    kwargs.setdefault("port", 3306)
    kwargs.setdefault("charset", "utf8mb4")

    if not kwargs.get("host") or not kwargs.get("user") or kwargs.get("password") is None or not kwargs.get("database"):
        raise ValueError("MySQL connection requires host, user, password, and database")
    return kwargs


def _build_default_query(name: str, table: str) -> str:
    fields = MYSQL_DEFAULT_ENDPOINT_FIELDS.get(name)
    if not fields:
        raise ValueError(f"unknown endpoint for mysql query build: {name}")
    where = MYSQL_DEFAULT_ENDPOINT_WHERE.get(name, "")
    order_by = MYSQL_DEFAULT_ENDPOINT_ORDER.get(name)
    query = f"SELECT {fields} FROM {table}"
    if where:
        query += f" WHERE {where}"
    if order_by:
        query += f" ORDER BY {order_by}"
    return query


def build_mysql_endpoints(
    mysql_cfg: Mapping[str, Any],
    optional_endpoints: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    cfg = mysql_cfg or {}
    if not isinstance(cfg, Mapping):
        raise ValueError("data_source.mysql config must be a mapping")

    if str(cfg.get("schema", "")).strip().lower() == LEAN_CANONICAL_SCHEMA:
        return build_lean_canonical_endpoints(cfg, optional_endpoints)

    table_overrides = cfg.get("tables") if isinstance(cfg.get("tables"), Mapping) else {}
    endpoint_overrides = cfg.get("endpoints") if isinstance(cfg.get("endpoints"), Mapping) else {}
    configured_optional = cfg.get("optional_endpoints") if isinstance(cfg.get("optional_endpoints"), Mapping) else {}
    optional = optional_endpoints if optional_endpoints is not None else configured_optional

    endpoints: dict[str, dict[str, Any]] = {}
    for name in MYSQL_DEFAULT_ENDPOINT_FIELDS:
        spec = {
            "query": "",
            "required": bool(MYSQL_DEFAULT_ENDPOINT_REQUIRED.get(name, False)),
            "enabled": True,
        }

        override = endpoint_overrides.get(name) if isinstance(endpoint_overrides, Mapping) else {}
        override_has_enabled = False
        if isinstance(override, Mapping):
            if "query" in override and isinstance(override["query"], str):
                spec["query"] = override["query"].strip()
            if "required" in override:
                spec["required"] = bool(override["required"])
            if "enabled" in override:
                spec["enabled"] = bool(override["enabled"])
                override_has_enabled = True

        if name not in MYSQL_METADATA_ENDPOINTS and isinstance(optional, Mapping) and not override_has_enabled:
            spec["enabled"] = bool(optional.get(name, spec["enabled"]))

        if not spec["query"]:
            table = str(table_overrides.get(name, name)).strip() if isinstance(table_overrides, Mapping) else str(name)
            spec["query"] = _build_default_query(name, table)

        endpoints[name] = {
            "query": spec["query"],
            "required": bool(spec["required"]),
            "enabled": bool(spec["enabled"]),
        }
    return endpoints


class MysqlClient:
    def __init__(
        self,
        *,
        connection: Mapping[str, Any],
        endpoint_queries: Mapping[str, str],
        default_params: Mapping[str, Any] | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self.connection = dict(connection)
        self.endpoint_queries = dict(endpoint_queries)
        self.default_params = dict(default_params or {})
        self.retry = retry_policy or RetryPolicy()

    def _connect(self):
        try:
            import pymysql
        except ImportError as exc:  # pragma: no cover - optional path
            raise RuntimeError("pymysql is required for lean_mysql. Install project extra: .[data] or .[all]") from exc

        kwargs = self.connection.copy()
        kwargs.setdefault("cursorclass", pymysql.cursors.DictCursor)
        return pymysql.connect(**kwargs)

    def fetch(
        self,
        api_name: str,
        *,
        fields: str | None = None,
        required: bool = True,
        query: str | None = None,
        **params: Any,
    ) -> FetchResult:
        _ = fields
        prepared_query = query or self.endpoint_queries.get(api_name)
        if not prepared_query:
            raise ValueError(f"No SQL query configured for endpoint {api_name}")
        attempts = 0
        prepared_params = _coerce_params({**self.default_params, **params})
        for attempt in range(1, self.retry.max_attempts + 1):
            attempts = attempt
            try:
                with self._connect() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute(prepared_query, prepared_params)
                        rows = cursor.fetchall()
                data = pd.DataFrame(rows)
                return FetchResult(data, "empty" if data.empty else "success", attempt)
            except Exception as exc:  # pragma: no cover - runtime dependency path
                if attempt >= self.retry.max_attempts:
                    if required:
                        raise RuntimeError(f"{api_name} failed after {attempt} attempts: {exc}") from exc
                    return FetchResult(pd.DataFrame(), "failed", attempt, str(exc))
        return FetchResult(pd.DataFrame(), "failed", attempts, f"{api_name} unknown failure")

    def call(self, api_name: str, *, fields: str | None = None, required: bool = True, **params: Any) -> pd.DataFrame:
        return self.fetch(api_name, fields=fields, required=required, **params).data


def lean_mysql_preflight(
    mysql_cfg: Mapping[str, Any], start_date: str, end_date: str, *, universe: str | None = None
) -> dict[str, Any]:
    """Run bounded, read-only coverage checks against Lean canonical tables."""
    client = MysqlClient(connection=build_connection_kwargs(mysql_cfg), endpoint_queries={})
    required_tables = [
        "securities", "trade_calendar", "ashare_daily_bars", "adjustment_factors",
        "ashare_trade_status", "factor_values", "universe_membership", "market_daily_bars",
    ]
    source = str(mysql_cfg.get("source", "tushare")).strip()
    selected_universe = str(universe or mysql_cfg.get("universe", "CSI300")).strip()
    checks: dict[str, Any] = {}
    with client._connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema=%(database)s",
                {"database": client.connection["database"]},
            )
            available = {
                str(row.get("table_name") or row.get("TABLE_NAME") or next(iter(row.values())))
                for row in cursor.fetchall()
            }
            missing = sorted(set(required_tables) - available)
            if missing:
                return {"passed": False, "missing_tables": missing, "checks": {}}
            queries = {
                "bars": "SELECT COUNT(*) rows_count,COUNT(DISTINCT b.symbol) symbols,COUNT(DISTINCT b.trade_date) dates,MIN(b.trade_date) first_date,MAX(b.trade_date) last_date FROM ashare_daily_bars b WHERE b.adjust='raw' AND b.source=%(source)s AND b.trade_date BETWEEN %(start)s AND %(end)s AND EXISTS (SELECT 1 FROM universe_membership u WHERE u.universe_code=%(universe)s AND u.symbol=b.symbol AND u.start_date<=b.trade_date AND (u.end_date IS NULL OR u.end_date>=b.trade_date) AND (u.announce_date IS NULL OR u.announce_date<=b.trade_date) AND (u.effective_date IS NULL OR u.effective_date<=b.trade_date))",
                "benchmark": "SELECT COUNT(*) rows_count,MIN(trade_date) first_date,MAX(trade_date) last_date FROM market_daily_bars WHERE symbol='000300' AND asset_class='index' AND trade_date BETWEEN %(start)s AND %(end)s",
                "membership": "SELECT COUNT(*) rows_count,COUNT(DISTINCT symbol) symbols,MIN(start_date) first_date,MAX(COALESCE(end_date,start_date)) last_date FROM universe_membership WHERE universe_code=%(universe)s",
                "calendar": "SELECT SUM(is_open) rows_count,MIN(trade_date) first_date,MAX(trade_date) last_date FROM trade_calendar WHERE market='china' AND trade_date BETWEEN %(start)s AND %(end)s",
            }
            params = {"source": source, "universe": selected_universe, "start": pd.Timestamp(start_date).strftime("%Y-%m-%d"), "end": pd.Timestamp(end_date).strftime("%Y-%m-%d")}
            for name, query in queries.items():
                cursor.execute(query, params)
                checks[name] = dict(cursor.fetchone() or {})
    passed = all(int((checks[name] or {}).get("rows_count") or 0) > 0 for name in checks)
    return {"passed": passed, "source": source, "universe": selected_universe, "start_date": start_date, "end_date": end_date, "checks": checks}


def fetch_lean_benchmark(mysql_cfg: Mapping[str, Any], symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    ticker = str(symbol).upper().replace("SH", "").replace("SZ", "").replace("BJ", "")
    client = MysqlClient(connection=build_connection_kwargs(mysql_cfg), endpoint_queries={})
    query = (
        "SELECT trade_date,open,high,low,close,volume,amount,source FROM market_daily_bars "
        "WHERE symbol=%(symbol)s AND asset_class='index' AND adjust='raw' "
        "AND trade_date BETWEEN %(start)s AND %(end)s ORDER BY trade_date"
    )
    with client._connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                {
                    "symbol": ticker,
                    "start": pd.Timestamp(start_date).strftime("%Y-%m-%d"),
                    "end": pd.Timestamp(end_date).strftime("%Y-%m-%d"),
                },
            )
            return pd.DataFrame(cursor.fetchall())
