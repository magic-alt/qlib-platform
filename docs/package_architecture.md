---
status: ACTIVE
owner: architecture
applies_to_commit: e3d9eeea02b5f6b4b13ea196c00a14aab9aa21b3
last_verified: 2026-09-04
---

# Python package architecture

`qlib-platform` is a research and alpha-factory package. Its canonical Python namespace is provider-neutral: `qlib_platform`. Market-data vendors are adapters, not package or domain identities.

## Canonical package layout

```text
src/qlib_platform/
├── cli/                                  # thin composition + domain command registrars
├── settings.py, lineage.py               # cross-domain composition/core
├── bootstrap.py, canonical_config.py
├── workflow_contract.py, project_audit.py
├── data/                                  # ingestion + normalized market-data plane
│   ├── ingestion.py
│   ├── fundamentals.py
│   ├── industry.py
│   ├── normalize.py, quality.py, store.py
│   └── sources/
│       ├── base.py                        # DataSourceClient/FetchResult/RetryPolicy
│       ├── registry.py                    # provider registry and factory
│       ├── tushare.py                     # Tushare Pro adapter
│       └── mysql.py                       # MySQL / Lean canonical adapter
├── datasets/                              # DatasetVersion and data lifecycle
│   ├── dataset_manifest.py
│   ├── dataset_registry.py
│   ├── dataset_resolver.py
│   ├── data_release.py
│   ├── qlib_export.py
│   └── layout_migration.py
├── backtesting/                           # portfolio, strategy, audit, backtest/reporting
├── artifacts/                             # artifact contracts and research/live artifacts
├── research/                              # feature/train/walk-forward/diagnostics/governance
├── models/                                # model adapters/runtime/bundles/registry/refit
├── runtime/                               # live inference, health, monitoring, schedulers
├── ops/                                   # operational state and platform/LEAN integration
├── releases/                              # immutable release stores and publication
├── platform_adapter/                      # cross-repository artifact handoff
├── feedback/                              # realized labels and production feedback
├── auth/
└── notifier/
```

The package root is intentionally small. It is a composition boundary for CLI/configuration/lineage and other genuinely cross-domain surfaces; implementation modules belong in the domain that owns them.

The historical `src/tushare_qlib` compatibility namespace has been removed. New code must import the canonical domain path directly. Do not reintroduce a vendor-named package or a dynamic import hook to emulate the deleted namespace.

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

## Data-source boundary

The ingestion plane depends on the `DataSourceClient` protocol rather than on Tushare Pro directly. A pull-style provider returns a normalized `FetchResult` from `fetch()` and exposes `call()` for the existing tabular ingestion contract.

`Extractor` resolves its provider through `data.sources.create_data_source()`. Built-in factories currently provide:

- `tushare` — Tushare Pro adapter;
- `mysql`, including the `lean_mysql`, `lean-platform`, and `lean_platform` aliases — read-only MySQL / Lean canonical adapter.

`data_source.kind: auto` preserves the current source-selection behavior: use configured MySQL when available, otherwise fall back to Tushare.

### Adding another provider

A new provider should:

1. implement the `DataSourceClient` contract;
2. translate provider-native fields into the logical endpoint schema consumed by the pipeline;
3. register a factory with `register_data_source()`;
4. keep credentials and transport-specific retry/rate-limit behavior inside the adapter;
5. add deterministic tests that do not require a live vendor connection.

Do not add `if provider == ...` branches to the ingestion orchestrator. Provider-specific optimizations belong in adapter capabilities or endpoint overrides.

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

Existing top-level `tushare:` settings remain accepted as a configuration migration fallback. New configuration should use the provider-neutral `data_source` hierarchy.

## Provider-neutral storage layout

The canonical mutable market-data working view is now:

```text
data/bronze/market/current/
```

The directory identifies the semantic layer (`market`) rather than the API vendor used to populate it. Provider provenance remains in manifests/configuration and adapter metadata where it belongs.

Existing installations may still contain either of these historical layouts:

```text
data/bronze/tushare/   # immediate pre-migration layout
data/raw/              # older pre-layered layout
```

`migrate-qlib-layout` handles these paths explicitly. The migration copies or hard-links into the provider-neutral target, verifies the complete file set, sizes and bytes/checksums, atomically publishes the target, journals the operation, and leaves the source untouched. If both historical market-data layouts are present, migration fails closed rather than merging potentially different histories.

This is deliberately a storage-layout migration, not a DatasetVersion rewrite. Existing manifests, immutable DatasetVersion identities, PIT semantics, parent relationships, and legacy source paths are not mutated merely because the canonical working-view directory changed.

## Compatibility policy

- `qlib_platform` is the only Python package namespace.
- `python -m qlib_platform` and the `qlib-platform` console script are canonical entry points.
- Existing `tq`, `tq-research`, `tq-research-summary`, and `tq-render-scheduler` console entry points remain available and resolve canonical `qlib_platform` modules.
- Provider-specific identifiers may remain where they represent actual provenance or an explicitly supported provider, but they must not define core package/domain/storage identity.
- Existing DatasetVersion names or IDs that contain historical provenance are not mechanically renamed; identity changes require their own governed migration.

This refactor does **not** intentionally change point-in-time causality, DatasetVersion identities, feature/model logic, strategy rules, backtest behavior, promotion gates, final-holdout policy, or the research/execution repository boundary.
