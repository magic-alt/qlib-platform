---
status: ACTIVE
owner: operations
applies_to_commit: 6707b01428886f4d43c6cabde23780ae80cab420
last_verified: 2026-09-16
---

# Production Config Ratchet

Issue #132 makes the production profile an explicit safety contract rather than a copy of the developer configuration. The contract is enforced by `Settings.load()` before directory creation or provider construction, so an unsafe production configuration fails before it can mutate local state.

## Profiles and environment identity

The repository exposes separate TuShare profiles for different trust levels:

| Profile | Environment | Purpose |
| --- | --- | --- |
| `configs/pipeline_tushare_dev.yaml` | `dev` | Local development and exploratory research. |
| `configs/pipeline_tushare_ci.yaml` | `ci` | Fixture-oriented CI without production credentials or publication. |
| `configs/pipeline_tushare_prod.yaml` | `prod` | Strict production ingestion and immutable publication. |

Production never falls back to a developer working directory. Set an isolated root and the TuShare credential before resolving the profile:

```bash
export QLIB_PROD_ROOT=/srv/qlib-platform/prod
export TUSHARE_TOKEN='...'
qlib-platform --config configs/pipeline_tushare_prod.yaml --plan
```

On PowerShell:

```powershell
$env:QLIB_PROD_ROOT = 'D:\qlib-platform\prod'
$env:TUSHARE_TOKEN = '...'
qlib-platform --config configs/pipeline_tushare_prod.yaml --plan
```

`--plan` uses `create_dirs=False`. It validates the fully resolved production policy and emits `writeMode: NONE`; it must not create the production root. The plan records only the credential reference name (`TUSHARE_TOKEN`), never the credential value.

## Fail-closed production preflight

For `environment: prod`, preflight rejects the configuration before any filesystem write when any of the following is true:

- the production root reference is missing or unresolved;
- TuShare credentials are inline, the secret reference is missing, or the referenced environment secret is unavailable;
- `daily`, `adj_factor`, or `daily_basic` is disabled;
- retry/backoff is unbounded or invalid;
- an unsafe test/development bypass is enabled, including `test_coverage_mode`, `offline_on_empty`, quality bypasses, or in-place rewrite/migration modes;
- release publication is mutable or `publish_on_sync` is disabled;
- artifact, cache, or log roots escape `QLIB_PROD_ROOT`;
- the production config/policy schema is unknown or lacks an explicit migration/rollback contract.

The validator reports field paths and policy violations. It does not include secret values in errors or resolved plan output.

## Required endpoint completeness

Production uses per-endpoint publication gates rather than a generic non-empty check. `daily`, `adj_factor`, and `daily_basic` each declare:

- `max_staleness_sessions: 0` — the target trading session must be present;
- `min_rows` — the target partition must contain a minimum cross-section;
- `min_previous_session_ratio` — the target row count must remain within the configured fraction of the previous trading session.

The current repository production baseline is `min_rows: 250` and `min_previous_session_ratio: 0.95` for all three required endpoints. The resolved values are embedded in the daily sync plan under `production_policy` and the runtime freshness report under `required_endpoint_policy`.

A required-endpoint failure blocks publication. Optional enrichments may retain their existing degraded-mode semantics only when they are not part of the required production set.

## Immutable publication sequence

Production reuses the existing release and registry primitives; there is no second production publisher.

1. Build the candidate dataset without activating it.
2. `LocalReleasePublisher` writes a temporary `.building-release.*` tree and validates the candidate.
3. The validated candidate is finalized as a content-addressed `ds_*` DataRelease using an atomic filesystem replace. Published release content is not rewritten in place.
4. Register the DataRelease and its DatasetVersion.
5. `DatasetRegistry.promote_research_snapshot()` advances the release alias and dataset alias in one SQLite transaction.

The active alias is therefore the last step, not the first. A build, validation, registration, or transactional promotion failure leaves the previous `production-current` release/dataset aliases active. The regression suite injects a failure between dataset-alias and release-alias updates and verifies the transaction rolls both changes back.

## Path isolation

The production root is resolved exclusively through `project_root_env: QLIB_PROD_ROOT`. Empty production storage fields intentionally derive from that root:

- registry: `<QLIB_PROD_ROOT>/registry/qlib.sqlite`;
- immutable releases: `<QLIB_PROD_ROOT>/releases`;
- Qlib active dataset: `<QLIB_PROD_ROOT>/qlib/current`;
- Qlib versions: `<QLIB_PROD_ROOT>/qlib/versions`;
- state: `<QLIB_PROD_ROOT>/state`;
- quality evidence: `<QLIB_PROD_ROOT>/quality`;
- output: `<QLIB_PROD_ROOT>/output`;
- artifacts/cache/logs: roots declared under `production_policy.paths`, constrained to stay below `QLIB_PROD_ROOT`.

This prevents a production command from silently sharing developer, CI, home-directory, or current-working-directory state.

## Timezones and scheduling

Two timezones are explicit:

- `data_sync.timezone` is the exchange/business-date timezone used for trading-session readiness;
- `production.daily_run.schedule.timezone` is the scheduler timezone.

The TuShare production profile uses `Asia/Shanghai` for both. Changing either is a reviewed configuration change rather than an implicit host-local setting.

## Retention and garbage collection policy

Production declares independent retention windows for:

- Bronze raw market data;
- Silver curated/reference data;
- immutable DataReleases;
- research/operational artifacts;
- logs.

`release_min_count` retains a minimum number of immutable releases even when they are older than the time window. `protect_referenced_objects: true` is mandatory: garbage collection must not remove a release or artifact still referenced by a run manifest, dataset lineage record, or active alias.

Issue #132 defines the retention contract; destructive GC remains a separate operational action and must consume this policy rather than infer its own defaults.

## Config schema migration and rollback

Both the root config and production policy are versioned (`1.0`). Production requires:

```yaml
config_migration:
  unknown_version: fail
  defaults_change: explicit_migration
  rollback: previous_config_and_active_release
```

A future schema change must therefore include an explicit migration. Silent reinterpretation of an old production YAML under new defaults is not allowed. Rollback consists of restoring the previously reviewed config and retaining/pointing back to the previously active immutable release; release content itself is never edited to perform a rollback.

## Operator checklist

Before a scheduled or manual production run:

1. Set `QLIB_PROD_ROOT` to the isolated production root and provide `TUSHARE_TOKEN` through the secret manager/environment.
2. Run `qlib-platform --config configs/pipeline_tushare_prod.yaml --plan` and verify `environment=prod`, `writeMode=NONE`, resolved roots, retry policy, endpoint coverage gates, retention, and timezones.
3. Do not proceed if any preflight violation is reported.
4. Run the normal audited daily pipeline; do not bypass required endpoint or quality gates.
5. Confirm the run manifest records the new immutable DataRelease/DatasetVersion and that `production-current` moved only after all publication checks succeeded.
6. On failure, investigate the rejected candidate while continuing to serve the previously active immutable release.
