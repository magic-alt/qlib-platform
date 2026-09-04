from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "qlib_platform"


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def migrate_ingestion() -> None:
    legacy_path = PACKAGE / "data" / "_legacy_ingestion.py"
    target_path = PACKAGE / "data" / "ingestion.py"
    if not legacy_path.is_file():
        raise RuntimeError("legacy ingestion source is missing")
    source = legacy_path.read_text(encoding="utf-8")

    old_imports = '''from qlib_platform.data.sources import DataSourceClient, RetryPolicy
from qlib_platform.data.sources.tushare import TushareClient
from qlib_platform.data.sources.mysql import (
    MysqlClient,
    build_connection_kwargs,
    build_lean_canonical_range_endpoints,
    build_mysql_endpoints,
    fetch_lean_benchmark,
    fetch_lean_universe_intervals,
    lean_mysql_preflight,
)
'''
    source = replace_once(
        source,
        old_imports,
        "from qlib_platform.data.sources import DataSourceClient, RetryPolicy, create_data_source\n",
        label="ingestion provider imports",
    )
    source = source.replace(
        'INDEX_DAILY_FIELDS = "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"\n'
        'INDEX_DAILY_FIELDS = "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"\n',
        'INDEX_DAILY_FIELDS = "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"\n',
    )

    helper_block = '''\n\ndef _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _source_runtime_config(settings: Settings) -> Mapping[str, Any]:
    source_cfg = _mapping(settings.data.get("data_source"))
    runtime = source_cfg.get("runtime")
    if isinstance(runtime, Mapping):
        return runtime
    return _mapping(settings.data.get("tushare"))


def _optional_endpoints(settings: Settings) -> Mapping[str, Any]:
    source_cfg = _mapping(settings.data.get("data_source"))
    value = source_cfg.get("optional_endpoints")
    if isinstance(value, Mapping):
        return value
    legacy = _mapping(settings.data.get("tushare"))
    value = legacy.get("optional_endpoints")
    return value if isinstance(value, Mapping) else {}


def _provider_config(settings: Settings, provider: str) -> Mapping[str, Any]:
    source_cfg = _mapping(settings.data.get("data_source"))
    return _mapping(source_cfg.get(provider))
'''
    source = replace_once(
        source,
        "\n\n@dataclass(frozen=True)\nclass Endpoint:",
        helper_block + "\n\n@dataclass(frozen=True)\nclass Endpoint:",
        label="ingestion helpers insertion",
    )

    class_start = source.index("class Extractor:\n")
    fetch_start = source.index("    def fetch_stock_master", class_start)
    class_header = '''class Extractor:
    """Provider-neutral ingestion orchestrator over the DataSource registry."""

    def __init__(self, settings: Settings):
        runtime = _source_runtime_config(settings)
        retry_policy = RetryPolicy(
            int(runtime.get("max_attempts", 6)),
            float(runtime.get("base_sleep_seconds", 2.0)),
            float(runtime.get("max_sleep_seconds", 60.0)),
            float(runtime.get("jitter_ratio", 0.15)),
        )
        binding = create_data_source(settings, retry_policy)
        self.settings = settings
        self.store = PartitionStore(settings.paths.raw)
        self.binding = binding
        self.client: DataSourceClient = binding.client
        self.source_is_mysql = "mysql" in binding.capabilities

        optional = _optional_endpoints(settings)
        endpoints = [
            Endpoint("daily", DAILY_FIELDS, True, enabled=bool(optional.get("daily", True))),
            Endpoint("adj_factor", ADJ_FIELDS, True, enabled=bool(optional.get("adj_factor", True))),
            Endpoint("daily_basic", BASIC_FIELDS, True, enabled=bool(optional.get("daily_basic", True))),
            Endpoint("moneyflow", MONEYFLOW_FIELDS, False, enabled=bool(optional.get("moneyflow", True))),
            Endpoint("stk_limit", LIMIT_FIELDS, False, enabled=bool(optional.get("stk_limit", True))),
            Endpoint("suspend_d", SUSPEND_FIELDS, False, enabled=bool(optional.get("suspend_d", True))),
            Endpoint("stock_st", ST_FIELDS, False, enabled=bool(optional.get("stock_st", True))),
        ]
        self.endpoints = []
        for endpoint in endpoints:
            override = binding.endpoint_overrides.get(endpoint.name)
            if override is None:
                self.endpoints.append(endpoint)
                continue
            self.endpoints.append(
                Endpoint(
                    endpoint.name,
                    endpoint.fields,
                    endpoint.required if override.required is None else override.required,
                    endpoint.enabled if override.enabled is None else override.enabled,
                )
            )

    def _operation(self, name: str):
        operation = self.binding.operations.get(name)
        if operation is None:
            raise ValueError(f"data source {self.binding.name!r} does not support operation {name!r}")
        return operation

'''
    source = source[:class_start] + class_header + source[fetch_start:]

    old_open_dates = '''    def open_dates(self, start_date: str, end_date: str) -> list[str]:
        path = self.settings.paths.metadata / "trade_calendar.parquet"
        if not path.exists():
            self.fetch_calendar(start_date, end_date)
        frame = pd.read_parquet(path)
        frame = frame[(frame["cal_date"] >= start_date) & (frame["cal_date"] <= end_date)]
        frame = frame[frame["is_open"].astype(int) == 1]
        return sorted(frame["cal_date"].astype(str).unique().tolist())
'''
    new_open_dates = '''    def open_dates(self, start_date: str, end_date: str) -> list[str]:
        """Return open market dates in the canonical ``YYYYMMDD`` partition format."""

        path = self.settings.paths.metadata / "trade_calendar.parquet"
        if not path.exists():
            self.fetch_calendar(start_date, end_date)
        frame = pd.read_parquet(path)
        calendar_dates = pd.to_datetime(frame["cal_date"], errors="raise").dt.normalize()
        start = pd.Timestamp(start_date).normalize()
        end = pd.Timestamp(end_date).normalize()
        mask = calendar_dates.between(start, end) & frame["is_open"].astype(int).eq(1)
        return sorted(calendar_dates.loc[mask].dt.strftime("%Y%m%d").unique().tolist())
'''
    source = replace_once(source, old_open_dates, new_open_dates, label="open_dates")

    source = source.replace(
        "lean_mysql_preflight(mysql_cfg, dates[0], dates[-1])",
        'self._operation("preflight")(mysql_cfg, dates[0], dates[-1])',
    )
    source = source.replace(
        'optional = self.settings.data["tushare"].get("optional_endpoints", {})\n'
        "        definitions = build_lean_canonical_range_endpoints(mysql_cfg, optional)",
        'optional = _optional_endpoints(self.settings)\n'
        '        definitions = self._operation("build_range_endpoints")(mysql_cfg, optional)',
    )
    source = source.replace(
        "return lean_mysql_preflight(mysql_cfg, start_date, end_date)",
        'return self._operation("preflight")(mysql_cfg, start_date, end_date)',
    )
    source = source.replace(
        "frame = fetch_lean_benchmark(mysql_cfg, normalized_symbol, start_date, end_date)",
        'frame = self._operation("fetch_benchmark")(mysql_cfg, normalized_symbol, start_date, end_date)',
    )
    source = source.replace(
        "source_intervals = fetch_lean_universe_intervals(\n                    mysql_cfg,",
        'source_intervals = self._operation("fetch_universe_intervals")(\n                    mysql_cfg,',
    )

    forbidden = (
        "qlib_platform.data.sources.tushare",
        "qlib_platform.data.sources.mysql",
        "TushareClient",
        "MysqlClient",
        "_LegacyExtractor",
    )
    present = [token for token in forbidden if token in source]
    if present:
        raise RuntimeError(f"canonical ingestion still references concrete provider/legacy symbols: {present}")

    target_path.write_text(source, encoding="utf-8")
    legacy_path.unlink()


def migrate_source_registry() -> None:
    path = PACKAGE / "data" / "sources" / "registry.py"
    source = path.read_text(encoding="utf-8")
    source = replace_once(
        source,
        "    capabilities: frozenset[str] = field(default_factory=frozenset)\n",
        "    capabilities: frozenset[str] = field(default_factory=frozenset)\n"
        "    operations: Mapping[str, Callable[..., Any]] = field(default_factory=dict)\n",
        label="binding operations field",
    )
    old_tushare = '''    client = TushareClient(
        settings.require_token(),
        calls_per_minute=calls,
        retry_policy=retry_policy,
    )
'''
    new_tushare = '''    token_env = str(provider_cfg.get("token_env") or "TUSHARE_TOKEN").strip()
    token = os.getenv(token_env, "").strip() or str(settings.tushare_token or "").strip()
    if not token:
        raise RuntimeError(
            f"{token_env} is not set. Configure data_source.tushare.token_env or the environment variable."
        )
    client = TushareClient(
        token,
        calls_per_minute=calls,
        retry_policy=retry_policy,
    )
'''
    source = replace_once(source, old_tushare, new_tushare, label="tushare credential resolution")
    source = replace_once(
        source,
        "    from qlib_platform.data.sources.mysql import MysqlClient, build_connection_kwargs, build_mysql_endpoints\n",
        "    from qlib_platform.data.sources.mysql import (\n"
        "        MysqlClient,\n"
        "        build_connection_kwargs,\n"
        "        build_lean_canonical_range_endpoints,\n"
        "        build_mysql_endpoints,\n"
        "        fetch_lean_benchmark,\n"
        "        fetch_lean_universe_intervals,\n"
        "        lean_mysql_preflight,\n"
        "    )\n",
        label="mysql factory imports",
    )
    old_return = '''    return DataSourceBinding(
        name="mysql",
        client=client,
        endpoint_overrides=overrides,
        capabilities=frozenset({"mysql"}),
    )
'''
    new_return = '''    return DataSourceBinding(
        name="mysql",
        client=client,
        endpoint_overrides=overrides,
        capabilities=frozenset({"mysql"}),
        operations={
            "preflight": lean_mysql_preflight,
            "build_range_endpoints": build_lean_canonical_range_endpoints,
            "fetch_benchmark": fetch_lean_benchmark,
            "fetch_universe_intervals": fetch_lean_universe_intervals,
        },
    )
'''
    source = replace_once(source, old_return, new_return, label="mysql operations binding")
    path.write_text(source, encoding="utf-8")


def migrate_settings() -> None:
    path = PACKAGE / "settings.py"
    source = path.read_text(encoding="utf-8")
    source = source.replace("import re\n", "import re\nimport warnings\n", 1)
    source = replace_once(
        source,
        "    tushare_token: str | None\n",
        "    # Deprecated constructor compatibility only; adapters resolve provider credentials.\n"
        "    tushare_token: str | None\n",
        label="settings compatibility field",
    )
    source = replace_once(
        source,
        "        data = _expand_env(_load_config(config_path))\n\n        if \"project_root\" not in data:\n",
        "        data = _expand_env(_load_config(config_path))\n"
        "        source_cfg = data.get(\"data_source\", {})\n"
        "        source_cfg = source_cfg if isinstance(source_cfg, dict) else {}\n"
        "        if isinstance(data.get(\"tushare\"), dict) and not isinstance(source_cfg.get(\"tushare\"), dict):\n"
        "            warnings.warn(\n"
        "                \"top-level tushare configuration is deprecated; move provider settings under data_source.tushare and retry/endpoint settings under data_source.runtime/optional_endpoints\",\n"
        "                DeprecationWarning,\n"
        "                stacklevel=2,\n"
        "            )\n\n"
        "        if \"project_root\" not in data:\n",
        label="settings deprecation warning",
    )
    require_pattern = re.compile(
        r"\n    def require_token\(self\) -> str:\n"
        r"        if not self\.tushare_token:\n"
        r"            raise RuntimeError\([^\n]+\)\n"
        r"        return self\.tushare_token\n"
    )
    source, count = require_pattern.subn("\n", source, count=1)
    if count != 1:
        raise RuntimeError(f"settings require_token removal: expected one match, found {count}")
    marker = "    def uses_tushare_source(self) -> bool:\n"
    helpers = '''    @property
    def data_source_config(self) -> dict[str, Any]:
        value = self.data.get("data_source", {})
        return value if isinstance(value, dict) else {}

    def provider_config(self, provider: str | None = None) -> dict[str, Any]:
        name = (provider or self.source_kind).strip().lower().replace("-", "_")
        value = self.data_source_config.get(name, {})
        if isinstance(value, dict):
            return value
        return {}

'''
    source = replace_once(source, marker, helpers + marker, label="settings provider helpers")
    path.write_text(source, encoding="utf-8")


def remove_source_client_shim() -> None:
    shim = PACKAGE / "data" / "sources" / "client.py"
    for path in list(PACKAGE.rglob("*.py")) + list((ROOT / "tests").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        updated = source.replace("qlib_platform.data.sources.client", "qlib_platform.data.sources.base")
        if updated != source:
            path.write_text(updated, encoding="utf-8")
    if shim.exists():
        shim.unlink()


COMMAND_DOMAINS = {
    "init-metadata": "data",
    "backfill": "data",
    "backfill-extended": "data",
    "source-preflight": "data",
    "sync-benchmark": "data",
    "sync-universe": "data",
    "sync-industry": "data",
    "daily-sync": "data",
    "sync-dividends": "data",
    "export-kline": "data",
    "curate": "data",
    "curate-day": "data",
    "stage-full": "data",
    "dump-full": "data",
    "stage-update": "data",
    "dump-update": "data",
    "ingest-pit-fundamentals": "data",
    "bootstrap": "data",
    "migration-acceptance": "data",
    "feature-store": "datasets",
    "dataset-build": "datasets",
    "migrate-qlib-layout": "datasets",
    "dataset-list": "datasets",
    "dataset-show": "datasets",
    "dataset-verify": "datasets",
    "dataset-resolve": "datasets",
    "dataset-promote": "datasets",
    "registry-rebuild": "datasets",
    "train-select": "research",
    "research-run": "research",
    "research-report": "research",
    "alpha-diagnose": "research",
    "regime-diagnose": "research",
    "attribution-diagnose": "research",
    "explanation-diagnose": "research",
    "phase1-synthesize": "research",
    "phase2-validate": "research",
    "phase2-plan": "research",
    "phase2-data-accept": "research",
    "phase2-collect": "research",
    "phase2-accept": "research",
    "phase2-select": "research",
    "phase2-final-holdout-open": "research",
    "phase3-validate": "research",
    "phase3-plan": "research",
    "phase3-diagnose": "research",
    "phase3-portable-export": "research",
    "phase3-portable-verify": "research",
    "research-gate": "research",
    "research-audit": "research",
    "backtest-predictions": "backtesting",
    "build-target-portfolio": "backtesting",
    "lean-export": "backtesting",
    "runtime-probe": "runtime",
    "model-refit": "runtime",
    "model-deploy": "runtime",
    "model-rollback": "runtime",
    "model-status": "runtime",
    "status": "runtime",
    "health": "runtime",
    "live-inference": "runtime",
    "daily-signal-run": "runtime",
    "outbox": "ops",
    "auth": "ops",
    "lean-register": "ops",
    "artifact-v2-export": "ops",
    "project-audit": "ops",
    "validate-qrun-contract": "ops",
    "ops-query": "ops",
    "ops-retry-delivery": "ops",
    "ops-ack": "ops",
    "ops-summary": "ops",
    "release": "releases",
    "feedback-build-labels": "feedback",
    "feedback-evaluate": "feedback",
}


def _call_receiver(call: ast.Call) -> tuple[str | None, str | None]:
    func = call.func
    if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Name):
        return None, None
    return func.value.id, func.attr


def _literal_first_arg(call: ast.Call) -> str | None:
    if not call.args:
        return None
    value = call.args[0]
    return value.value if isinstance(value, ast.Constant) and isinstance(value.value, str) else None


def split_cli() -> None:
    source_path = PACKAGE / "cli.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    parser_node = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "parser"
    )
    if parser_node.end_lineno is None:
        raise RuntimeError("parser AST has no end line")

    body = parser_node.body
    if len(body) < 4:
        raise RuntimeError("unexpected parser body")
    groups: dict[str, list[str]] = {name: [] for name in sorted(set(COMMAND_DOMAINS.values()))}
    var_domains: dict[str, str] = {}
    unclassified: list[str] = []

    for stmt in body:
        if isinstance(stmt, ast.Return):
            continue
        segment = ast.get_source_segment(source, stmt)
        if segment is None:
            raise RuntimeError("could not recover parser statement")
        if any(token in segment for token in ("argparse.ArgumentParser(", "p.add_argument(\"--config\"", "p.add_subparsers(")):
            continue

        domain: str | None = None
        call: ast.Call | None = None
        assigned_name: str | None = None
        if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Call):
            call = stmt.value
            if len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                assigned_name = stmt.targets[0].id
        elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            call = stmt.value

        if call is not None:
            receiver, attr = _call_receiver(call)
            if receiver == "sub" and attr == "add_parser":
                command = _literal_first_arg(call)
                domain = COMMAND_DOMAINS.get(command or "")
                if domain is None:
                    unclassified.append(command or segment.splitlines()[0])
            elif receiver in var_domains:
                domain = var_domains[receiver]
            if assigned_name is not None and domain is not None:
                var_domains[assigned_name] = domain

        if domain is None:
            unclassified.append(segment.splitlines()[0])
            continue
        groups[domain].append(textwrap.dedent(segment))

    if unclassified:
        raise RuntimeError(f"unclassified CLI parser statements: {unclassified}")

    cli_dir = PACKAGE / "cli"
    commands_dir = cli_dir / "commands"
    commands_dir.mkdir(parents=True, exist_ok=True)
    (commands_dir / "__init__.py").write_text('"""Domain-owned CLI parser registration."""\n', encoding="utf-8")

    for domain, statements in groups.items():
        module = (
            '"""Register CLI commands owned by the %s domain."""\n\n'
            "import argparse\n\n"
            "from qlib_platform.runtime.runtime_resources import resource_argument\n\n\n"
            "def register(sub) -> None:\n%s\n"
        ) % (domain, textwrap.indent("\n".join(statements), "    "))
        (commands_dir / f"{domain}.py").write_text(module, encoding="utf-8")

    domains = sorted(groups)
    parser_module = '''from __future__ import annotations

import argparse

from qlib_platform.runtime.runtime_resources import resource_argument
from qlib_platform.cli.commands import %s

COMMAND_REGISTRARS = (
%s
)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Auditable platform DataRelease -> Qlib research pipeline")
    root.add_argument("--config", default=resource_argument("configs/pipeline.standalone.yaml"))
    sub = root.add_subparsers(dest="command", required=True)
    for register in COMMAND_REGISTRARS:
        register(sub)
    return root
''' % (", ".join(domains), "\n".join(f"    {name}.register," for name in domains))
    (cli_dir / "parser.py").write_text(parser_module, encoding="utf-8")

    lines = source.splitlines(keepends=True)
    start = parser_node.lineno - 1
    end = parser_node.end_lineno
    main_source = "".join(lines[:start] + lines[end:])
    main_source = main_source.replace("import argparse\n", "", 1)
    main_source = main_source.replace(
        "from qlib_platform.runtime.runtime_resources import resource_argument\n",
        "from qlib_platform.cli.parser import parser\n",
        1,
    )
    (cli_dir / "main.py").write_text(main_source, encoding="utf-8")
    (cli_dir / "__init__.py").write_text(
        '"""CLI composition package; command parsers are registered by bounded domain."""\n\n'
        "from qlib_platform.cli.main import main\n"
        "from qlib_platform.cli.parser import parser\n\n"
        '__all__ = ["main", "parser"]\n',
        encoding="utf-8",
    )
    source_path.unlink()


def add_architecture_tests() -> None:
    path = ROOT / "tests" / "test_architecture_phase3.py"
    path.write_text(
        '''from __future__ import annotations

import ast
from pathlib import Path

from qlib_platform.settings import Paths


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "qlib_platform"


def test_package_root_is_a_small_composition_boundary():
    allowed = {
        "__init__.py",
        "__main__.py",
        "bootstrap.py",
        "canonical_config.py",
        "docs_check.py",
        "lineage.py",
        "project_audit.py",
        "settings.py",
        "workflow_contract.py",
    }
    files = {path.name for path in PACKAGE.iterdir() if path.is_file()}
    assert files <= allowed


def test_transitional_namespaces_and_shims_are_removed():
    assert not (ROOT / "src" / "tushare_qlib").exists()
    assert not (PACKAGE / "data" / "sources" / "client.py").exists()
    assert not list(PACKAGE.rglob("_legacy_*.py"))


def test_ingestion_does_not_import_concrete_provider_modules():
    path = PACKAGE / "data" / "ingestion.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert "qlib_platform.data.sources.tushare" not in imported
    assert "qlib_platform.data.sources.mysql" not in imported


def test_canonical_storage_identity_is_provider_neutral(tmp_path: Path):
    paths = Paths.from_root(tmp_path / "data")
    assert paths.bronze == tmp_path / "data" / "bronze" / "market"
    assert paths.raw == paths.bronze / "current"


def test_cli_is_composed_from_domain_registrars():
    parser_file = PACKAGE / "cli" / "parser.py"
    assert parser_file.is_file()
    for domain in ("data", "datasets", "research", "backtesting", "runtime", "ops", "releases"):
        assert (PACKAGE / "cli" / "commands" / f"{domain}.py").is_file()
''',
        encoding="utf-8",
    )


def update_docs() -> None:
    path = ROOT / "docs" / "package_architecture.md"
    source = path.read_text(encoding="utf-8")
    source = re.sub(r"applies_to_commit: [0-9a-f]+", "applies_to_commit: PHASE3_BRANCH", source, count=1)
    source = source.replace(
        "├── cli.py, settings.py, lineage.py       # cross-domain composition/core\n",
        "├── cli/                                  # thin composition + domain command registrars\n"
        "├── settings.py, lineage.py               # cross-domain composition/core\n",
    )
    insertion = '''
## Phase 3 architecture closure

Phase 3 removes the transitional ingestion inheritance layer and the historical
`data.sources.client` shim. `data.ingestion.Extractor` now owns the certified
orchestration logic directly, while provider construction and provider-specific
operations are resolved through `DataSourceBinding` from the source registry.
The canonical ingestion module must not import a concrete provider module.

The CLI is a package-level composition surface. `cli/main.py` owns dispatch,
`cli/parser.py` assembles the parser, and `cli/commands/` contains bounded-domain
command registration modules. New commands belong in the module for the domain
that owns their behavior rather than in one monolithic root parser.

`Settings.tushare_token` remains only as a deprecated constructor compatibility
field for existing direct `Settings(...)` callers. Runtime provider credentials
are resolved by the provider adapter/registry; `Settings.require_token()` has
been removed. Legacy top-level `tushare:` YAML remains readable for a migration
window and emits a deprecation warning when no `data_source.tushare` block is present.

### Large-file audit

- `data/sources/mysql.py` remains provider-local because its SQL schema translation,
  preflight and optimized range operations form one adapter boundary. Splitting it
  is deferred until a second SQL provider or independently reusable query families
  make the boundary concrete.
- `backtesting/backtest_report.py` remains intact because its report assembly is a
  cohesive output concern. A later split should be driven by independently tested
  renderers/export formats rather than file size alone.

Architectural regression tests prevent new root implementation modules, `_legacy_*`
Python modules, the removed vendor namespace/source-client shim, concrete-provider
imports from canonical ingestion, and provider-coupled canonical storage identity.
'''
    anchor = "\n## Data-source boundary\n"
    if insertion.strip() not in source:
        source = source.replace(anchor, insertion + anchor, 1)
    path.write_text(source, encoding="utf-8")


def main() -> None:
    migrate_source_registry()
    migrate_settings()
    migrate_ingestion()
    remove_source_client_shim()
    split_cli()
    add_architecture_tests()
    update_docs()
    print("Phase 3 architecture codemod applied")


if __name__ == "__main__":
    main()
