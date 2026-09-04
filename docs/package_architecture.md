---
status: ACTIVE
owner: architecture
applies_to_commit: df64d00844f63ecb1d3b6cc7169d5afab8cff829
last_verified: 2026-09-04
---

# Python package architecture

`qlib-platform` is a research and alpha-factory package. Its Python namespace is provider-neutral: the canonical package is `qlib_platform`, not a package named after any market-data vendor.

## Canonical package layout

```text
src/
├── qlib_platform/                 # canonical implementation namespace
│   ├── data/
│   │   ├── ingestion.py           # provider-neutral ingestion orchestration
│   │   ├── fundamentals.py        # PIT fundamentals materialization
│   │   ├── industry.py            # PIT industry classification
│   │   └── sources/
│   │       ├── base.py            # normalized client/result/retry contracts
│   │       ├── registry.py        # source registry + factory resolution
│   │       ├── tushare.py         # Tushare Pro adapter
│   │       └── mysql.py           # MySQL / Lean canonical adapter
│   ├── alpha/                     # alpha contracts and registry
│   ├── models/                    # model interfaces and adapters
│   ├── research/                  # research diagnostics and governed workflows
│   ├── releases/                  # immutable release contracts and stores
│   ├── platform_adapter/          # cross-repository artifact handoff
│   ├── feedback/                  # realized-label / evaluation feedback
│   └── ...                        # existing modules retained during staged migration
└── qlib_platform/                  # deprecated compatibility namespace only
```

The remaining root-level modules under `qlib_platform` are compatibility-preserving implementation surfaces from the original flat package. New functionality should be placed in a domain package rather than adding another root-level module. Subsequent refactors can migrate those modules domain-by-domain without another package-name migration.

## Data-source boundary

The ingestion plane depends on the `DataSourceClient` protocol rather than on Tushare Pro directly. A pull-style provider returns a normalized `FetchResult` from `fetch()` and exposes `call()` for the existing tabular ingestion contract.

`Extractor` resolves its provider through `data.sources.create_data_source()`. Built-in factories currently provide:

- `tushare` — Tushare Pro HTTP/SDK adapter;
- `mysql`, including the `lean_mysql`, `lean-platform`, and `lean_platform` aliases — read-only MySQL / Lean canonical adapter.

`data_source.kind: auto` preserves the previous behavior: use the configured MySQL source when one is present, otherwise fall back to Tushare.

### Adding another provider

A new provider should:

1. implement the `DataSourceClient` contract;
2. translate provider-native fields into the logical endpoint schema consumed by the pipeline;
3. register a factory with `register_data_source()`;
4. keep credentials and transport-specific retry/rate-limit behavior inside the adapter;
5. add deterministic tests that do not require a live vendor connection.

No new `if provider == ...` branch should be added to the ingestion orchestrator. Provider-specific optimizations should be represented as adapter capabilities or endpoint overrides.

## Configuration direction

Provider-independent runtime settings belong under `data_source`, for example:

```yaml
data_source:
  kind: tushare
  runtime:
    max_attempts: 6
    base_sleep_seconds: 2.0
    max_sleep_seconds: 60.0
    jitter_ratio: 0.15
  optional_endpoints:
    moneyflow: true
    stk_limit: true
  tushare:
    calls_per_minute: 180
```

Existing top-level `tushare:` settings remain accepted as a compatibility fallback during migration. New configuration should use the provider-neutral `data_source` hierarchy.

## Compatibility policy

- New code and documentation use `qlib_platform`.
- `python -m qlib_platform` and the `qlib-platform` console script are canonical entry points.
- Existing `tq`, `tq-research`, `tq-research-summary`, and `tq-render-scheduler` entry points remain available.
- `qlib_platform` is retained only as an import compatibility namespace so downstream users do not need a flag-day migration.
- The compatibility namespace should not receive new implementation modules.

This migration deliberately does not change point-in-time semantics, DatasetVersion identities, feature/model logic, strategy rules, backtest behavior, promotion gates, or the research/execution repository boundary.
