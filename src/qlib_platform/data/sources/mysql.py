from __future__ import annotations

import importlib
import os
import re
from datetime import date
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse, unquote

import pandas as pd

from qlib_platform.data.sources.client import FetchResult, RetryPolicy

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


def _source_filter(alias: str, *, include_derived: bool = False) -> str:
    exact = f"{alias}.source=%(source)s"
    if include_derived:
        exact += f" OR {alias}.source LIKE CONCAT(%(source)s,':%%')"
    return f"(%(source)s='' OR {exact})"


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
    date_eq = "b.trade_date=%(trade_date_iso)s"
    source = _source_filter("b")
    queries = {
        "daily": (
            f"SELECT {symbol} ts_code,REPLACE(b.trade_date,'-','') trade_date,b.open,b.high,b.low,b.close,"
            "b.prev_close pre_close,(b.close-b.prev_close) `change`,b.pct_change pct_chg,"
            "b.volume/100.0 vol,b.amount/1000.0 amount FROM ashare_daily_bars b "
            f"WHERE b.adjust='raw' AND {date_eq} AND {source} AND {pit} ORDER BY b.trade_date,b.symbol"
        ),
        "adj_factor": (
            f"SELECT {_symbol_sql('a')} ts_code,REPLACE(a.trade_date,'-','') trade_date,a.adj_factor "
            "FROM adjustment_factors a WHERE a.trade_date=%(trade_date_iso)s "
            f"AND {_source_filter('a')} "
            f"AND {_pit_filter('a')} ORDER BY a.trade_date,a.symbol"
        ),
        "daily_basic": (
            f"SELECT {_symbol_sql('b')} ts_code,REPLACE(b.trade_date,'-','') trade_date,NULL close,"
            "COALESCE(MAX(CASE WHEN f.factor_name='turnover_rate' THEN f.value END),b.turnover_rate) turnover_rate,"
            "MAX(CASE WHEN f.factor_name='turnover_rate_float' THEN f.value END) turnover_rate_f,"
            "MAX(CASE WHEN f.factor_name='volume_ratio' THEN f.value END) volume_ratio,"
            "MAX(CASE WHEN f.factor_name='pe' THEN f.value END) pe,"
            "MAX(CASE WHEN f.factor_name='pe_ttm' THEN f.value END) pe_ttm,"
            "MAX(CASE WHEN f.factor_name='pb' THEN f.value END) pb,"
            "MAX(CASE WHEN f.factor_name='ps' THEN f.value END) ps,"
            "MAX(CASE WHEN f.factor_name='ps_ttm' THEN f.value END) ps_ttm,"
            "MAX(CASE WHEN f.factor_name='dividend_yield' THEN f.value END) dv_ratio,"
            "MAX(CASE WHEN f.factor_name='dividend_yield_ttm' THEN f.value END) dv_ttm,"
            "MAX(CASE WHEN f.factor_name='total_share_shares' THEN f.value/10000.0 END) total_share,"
            "MAX(CASE WHEN f.factor_name='float_share_shares' THEN f.value/10000.0 END) float_share,"
            "MAX(CASE WHEN f.factor_name='free_share_shares' THEN f.value/10000.0 END) free_share,"
            "MAX(CASE WHEN f.factor_name='total_mv_cny' THEN f.value/10000.0 END) total_mv,"
            "MAX(CASE WHEN f.factor_name='circ_mv_cny' THEN f.value/10000.0 END) circ_mv,NULL limit_status "
            "FROM ashare_daily_bars b LEFT JOIN factor_values f ON f.symbol=b.symbol "
            "AND f.trade_date=b.trade_date "
            "AND (%(source)s='' OR f.source=CONCAT(%(source)s,':daily_basic') OR f.source=%(source)s) "
            "WHERE b.adjust='raw' AND b.trade_date=%(trade_date_iso)s "
            f"AND {_source_filter('b')} AND {_pit_filter('b')} "
            "GROUP BY b.symbol,b.trade_date,b.turnover_rate ORDER BY b.trade_date,b.symbol"
        ),
        "moneyflow": (
            "SELECT NULL ts_code,NULL trade_date,NULL buy_lg_vol,NULL buy_lg_amount,NULL sell_lg_vol,"
            "NULL sell_lg_amount,NULL buy_elg_vol,NULL buy_elg_amount,NULL sell_elg_vol,NULL sell_elg_amount,"
            "NULL net_mf_vol,NULL net_mf_amount WHERE 1=0"
        ),
        "stk_limit": (
            f"SELECT {_symbol_sql('t')} ts_code,REPLACE(t.trade_date,'-','') trade_date,NULL pre_close,"
            "t.limit_up up_limit,t.limit_down down_limit FROM ashare_trade_status t "
            "WHERE t.trade_date=%(trade_date_iso)s "
            f"AND {_source_filter('t', include_derived=True)} "
            f"AND {_pit_filter('t')} ORDER BY t.trade_date,t.symbol"
        ),
        "suspend_d": (
            f"SELECT {_symbol_sql('t')} ts_code,REPLACE(t.trade_date,'-','') trade_date,NULL suspend_timing,'S' suspend_type "
            "FROM ashare_trade_status t WHERE t.is_suspended=1 "
            "AND t.trade_date=%(trade_date_iso)s "
            f"AND {_source_filter('t', include_derived=True)} "
            f"AND {_pit_filter('t')} ORDER BY t.trade_date,t.symbol"
        ),
        "stock_st": (
            f"SELECT {_symbol_sql('t')} ts_code,s.name,REPLACE(t.trade_date,'-','') trade_date,'ST' type,'ST' type_name "
            "FROM ashare_trade_status t LEFT JOIN securities s ON s.symbol=t.symbol WHERE t.is_st=1 "
            "AND t.trade_date=%(trade_date_iso)s "
            f"AND {_source_filter('t', include_derived=True)} "
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
            "WHERE c.market='china' AND c.trade_date BETWEEN %(start_date_iso)s AND %(end_date_iso)s "
            "ORDER BY c.trade_date"
        ),
        "index_weight": (
            f"SELECT u.universe_code index_code,{_symbol_sql('u')} con_code,"
            "REPLACE(COALESCE(u.effective_date,u.start_date),'-','') trade_date,NULL weight "
            "FROM universe_membership u WHERE u.universe_code=%(index_code)s "
            "AND COALESCE(u.effective_date,u.start_date) "
            "BETWEEN %(start_date_iso)s AND %(end_date_iso)s ORDER BY trade_date,u.symbol"
        ),
    }
    optional = optional_endpoints or {}
    result: dict[str, dict[str, Any]] = {}
    for name, query in queries.items():
        enabled = bool(optional.get(name, True)) if name not in MYSQL_METADATA_ENDPOINTS else True
        if name == "moneyflow":
            enabled = bool(optional.get(name, False))
        result[name] = {
            "query": query,
            "required": name
            in {"daily", "adj_factor", "daily_basic", "stock_basic", "trade_cal", "index_weight"},
            "enabled": enabled,
        }
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
    result = {key: _coerce_param(value) for key, value in params.items()}
    for key in ("trade_date", "start_date", "end_date"):
        value = result.get(key)
        if value in (None, ""):
            continue
        try:
            result[f"{key}_iso"] = pd.Timestamp(str(value)).strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            # Legacy/custom endpoint queries still receive their original value;
            # only canonical Lean queries consume the companion ISO parameter.
            continue
    return result


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
    if config.get("readonly") is not True:
        raise ValueError("lean_mysql requires data_source.mysql.readonly: true")

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

    if (
        not kwargs.get("host")
        or not kwargs.get("user")
        or kwargs.get("password") is None
        or not kwargs.get("database")
    ):
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
    configured_optional = (
        cfg.get("optional_endpoints") if isinstance(cfg.get("optional_endpoints"), Mapping) else {}
    )
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

        if (
            name not in MYSQL_METADATA_ENDPOINTS
            and isinstance(optional, Mapping)
            and not override_has_enabled
        ):
            spec["enabled"] = bool(optional.get(name, spec["enabled"]))

        if not spec["query"]:
            table = (
                str(table_overrides.get(name, name)).strip()
                if isinstance(table_overrides, Mapping)
                else str(name)
            )
            spec["query"] = _build_default_query(name, table)

        endpoints[name] = {
            "query": spec["query"],
            "required": bool(spec["required"]),
            "enabled": bool(spec["enabled"]),
        }
    return endpoints


def build_lean_canonical_range_endpoints(
    mysql_cfg: Mapping[str, Any],
    optional_endpoints: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build range variants so a backfill scans each Lean table only once."""

    endpoints = build_lean_canonical_endpoints(mysql_cfg, optional_endpoints)
    replacements = {
        "b.trade_date=%(trade_date_iso)s": ("b.trade_date BETWEEN %(start_date_iso)s AND %(end_date_iso)s"),
        "a.trade_date=%(trade_date_iso)s": ("a.trade_date BETWEEN %(start_date_iso)s AND %(end_date_iso)s"),
        "f.trade_date=%(trade_date_iso)s": ("f.trade_date BETWEEN %(start_date_iso)s AND %(end_date_iso)s"),
        "t.trade_date=%(trade_date_iso)s": ("t.trade_date BETWEEN %(start_date_iso)s AND %(end_date_iso)s"),
    }
    for name in ("daily", "adj_factor", "daily_basic", "stk_limit", "suspend_d", "stock_st"):
        query = str(endpoints[name]["query"])
        for original, replacement in replacements.items():
            query = query.replace(original, replacement)
        endpoints[name] = {**endpoints[name], "query": query}
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
            pymysql = importlib.import_module("pymysql")
        except ImportError as exc:  # pragma: no cover - optional path
            raise RuntimeError(
                "pymysql is required for lean_mysql. Install project extra: .[data] or .[all]"
            ) from exc

        kwargs = self.connection.copy()
        kwargs.setdefault("cursorclass", pymysql.cursors.DictCursor)
        conn = pymysql.connect(**kwargs)
        # A YAML flag is not a security boundary.  Make every connection
        # read-only at the server session, so accidental DML fails even if an
        # endpoint override is misconfigured.
        with conn.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
        return conn

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
        if not re.match(r"^\s*(SELECT|WITH|SHOW|EXPLAIN)\b", prepared_query, flags=re.IGNORECASE):
            raise ValueError(f"endpoint {api_name} is not a read-only SQL statement")
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

    def call(
        self, api_name: str, *, fields: str | None = None, required: bool = True, **params: Any
    ) -> pd.DataFrame:
        return self.fetch(api_name, fields=fields, required=required, **params).data


def lean_mysql_preflight(
    mysql_cfg: Mapping[str, Any], start_date: str, end_date: str, *, universe: str | None = None
) -> dict[str, Any]:
    """Run bounded, read-only coverage checks against Lean canonical tables."""
    client = MysqlClient(connection=build_connection_kwargs(mysql_cfg), endpoint_queries={})
    required_tables = [
        "securities",
        "trade_calendar",
        "ashare_daily_bars",
        "adjustment_factors",
        "ashare_trade_status",
        "factor_values",
        "universe_membership",
        "market_daily_bars",
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
                "adjustment_factors": "SELECT COUNT(*) rows_count,COUNT(DISTINCT a.symbol) symbols,COUNT(DISTINCT a.trade_date) dates,MIN(a.trade_date) first_date,MAX(a.trade_date) last_date FROM adjustment_factors a WHERE a.source=%(source)s AND a.trade_date BETWEEN %(start)s AND %(end)s AND EXISTS (SELECT 1 FROM universe_membership u WHERE u.universe_code=%(universe)s AND u.symbol=a.symbol AND u.start_date<=a.trade_date AND (u.end_date IS NULL OR u.end_date>=a.trade_date) AND (u.announce_date IS NULL OR u.announce_date<=a.trade_date) AND (u.effective_date IS NULL OR u.effective_date<=a.trade_date))",
                "daily_basic": "SELECT COUNT(DISTINCT f.symbol,f.trade_date) rows_count,COUNT(DISTINCT f.symbol) symbols,COUNT(DISTINCT f.trade_date) dates,MIN(f.trade_date) first_date,MAX(f.trade_date) last_date FROM factor_values f WHERE f.source=CONCAT(%(source)s,':daily_basic') AND f.trade_date BETWEEN %(start)s AND %(end)s AND EXISTS (SELECT 1 FROM universe_membership u WHERE u.universe_code=%(universe)s AND u.symbol=f.symbol AND u.start_date<=f.trade_date AND (u.end_date IS NULL OR u.end_date>=f.trade_date) AND (u.announce_date IS NULL OR u.announce_date<=f.trade_date) AND (u.effective_date IS NULL OR u.effective_date<=f.trade_date))",
                "trade_status": "SELECT COUNT(*) rows_count,COUNT(DISTINCT t.symbol) symbols,COUNT(DISTINCT t.trade_date) dates,MIN(t.trade_date) first_date,MAX(t.trade_date) last_date FROM ashare_trade_status t WHERE (t.source=%(source)s OR t.source LIKE CONCAT(%(source)s,':%%')) AND t.trade_date BETWEEN %(start)s AND %(end)s AND EXISTS (SELECT 1 FROM universe_membership u WHERE u.universe_code=%(universe)s AND u.symbol=t.symbol AND u.start_date<=t.trade_date AND (u.end_date IS NULL OR u.end_date>=t.trade_date) AND (u.announce_date IS NULL OR u.announce_date<=t.trade_date) AND (u.effective_date IS NULL OR u.effective_date<=t.trade_date))",
                "benchmark": "SELECT COUNT(*) rows_count,MIN(trade_date) first_date,MAX(trade_date) last_date FROM market_daily_bars WHERE symbol='000300' AND asset_class='index' AND adjust='raw' AND source=%(source)s AND trade_date BETWEEN %(start)s AND %(end)s",
                "membership": "SELECT COUNT(*) rows_count,COUNT(DISTINCT symbol) symbols,MIN(start_date) first_date,MAX(COALESCE(end_date,start_date)) last_date FROM universe_membership WHERE universe_code=%(universe)s",
                "calendar": "SELECT SUM(is_open) rows_count,MIN(trade_date) first_date,MAX(trade_date) last_date FROM trade_calendar WHERE market='china' AND trade_date BETWEEN %(start)s AND %(end)s",
            }
            params = {
                "source": source,
                "universe": selected_universe,
                "start": pd.Timestamp(start_date).strftime("%Y-%m-%d"),
                "end": pd.Timestamp(end_date).strftime("%Y-%m-%d"),
            }
            for name, query in queries.items():
                cursor.execute(query, params)
                checks[name] = dict(cursor.fetchone() or {})
    coverage_failures: list[str] = []
    for name, check in checks.items():
        if int((check or {}).get("rows_count") or 0) <= 0:
            coverage_failures.append(f"{name}:empty")
    calendar_check = checks.get("calendar") or {}
    calendar_first = calendar_check.get("first_date")
    calendar_last = calendar_check.get("last_date")
    for name in ("bars", "adjustment_factors", "daily_basic", "trade_status", "benchmark"):
        check = checks.get(name) or {}
        if calendar_first and check.get("first_date") and str(check["first_date"]) > str(calendar_first):
            coverage_failures.append(f"{name}:starts_after_calendar")
        if calendar_last and check.get("last_date") and str(check["last_date"]) < str(calendar_last):
            coverage_failures.append(f"{name}:ends_before_calendar")
    passed = not coverage_failures
    return {
        "passed": passed,
        "source": source,
        "universe": selected_universe,
        "start_date": start_date,
        "end_date": end_date,
        "coverage_failures": coverage_failures,
        "checks": checks,
    }


def fetch_lean_benchmark(
    mysql_cfg: Mapping[str, Any], symbol: str, start_date: str, end_date: str
) -> pd.DataFrame:
    normalized = str(symbol).strip().upper()
    if re.fullmatch(r"(SH|SZ|BJ)\d{6}", normalized):
        ticker = normalized[2:]
    elif re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", normalized):
        ticker = normalized[:6]
    elif re.fullmatch(r"\d{6}", normalized):
        ticker = normalized
    else:
        raise ValueError(f"unsupported Lean benchmark symbol: {symbol}")
    client = MysqlClient(connection=build_connection_kwargs(mysql_cfg), endpoint_queries={})
    query = (
        "SELECT trade_date,open,high,low,close,prev_close pre_close,pct_change pct_chg,"
        "volume/100.0 vol,amount/1000.0 amount,source FROM market_daily_bars "
        "WHERE symbol=%(symbol)s AND asset_class='index' AND adjust='raw' "
        "AND (%(source)s='' OR source=%(source)s) "
        "AND trade_date BETWEEN %(start)s AND %(end)s ORDER BY trade_date"
    )
    with client._connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                {
                    "symbol": ticker,
                    "source": str(mysql_cfg.get("source", "tushare")).strip(),
                    "start": pd.Timestamp(start_date).strftime("%Y-%m-%d"),
                    "end": pd.Timestamp(end_date).strftime("%Y-%m-%d"),
                },
            )
            return pd.DataFrame(cursor.fetchall())


def fetch_lean_universe_intervals(
    mysql_cfg: Mapping[str, Any], universe: str, start_date: str, end_date: str
) -> pd.DataFrame:
    """Read the governed PIT membership intervals without treating deltas as snapshots."""

    client = MysqlClient(connection=build_connection_kwargs(mysql_cfg), endpoint_queries={})
    query = (
        "SELECT universe_code,symbol,start_date,end_date,announce_date,effective_date,weight,source "
        "FROM universe_membership WHERE universe_code=%(universe)s "
        "AND start_date<=%(end)s AND (end_date IS NULL OR end_date>=%(start)s) "
        "ORDER BY start_date,symbol"
    )
    with client._connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                {
                    "universe": universe,
                    "start": pd.Timestamp(start_date).strftime("%Y-%m-%d"),
                    "end": pd.Timestamp(end_date).strftime("%Y-%m-%d"),
                },
            )
            return pd.DataFrame(cursor.fetchall())
