---
status: ACTIVE
owner: architecture
applies_to_commit: 8692afefe1f6cc82ab1f276fca788888f9f30f3e
last_verified: 2026-08-28
---

# Standalone Sovereignty

`qlib-platform` defaults to `configs/pipeline.standalone.yaml`. Loading the
configuration, inspecting status, initializing local auth, and serving research-plane
health do not require `PLATFORM_URL`, `QUANT_DATA_ROOT`, `DATASET_RELEASE_ID`, or
`TUSHARE_TOKEN`.

## Source resolution

`bootstrap --source auto` resolves sources in this order:

1. active immutable local DataRelease and its bound DatasetVersion;
2. a single local DataRelease requiring materialization or promotion;
3. an existing local Qlib provider requiring immutable import;
4. local raw data: full governed inputs select `ashare_qlib_research_v2`;
5. OHLCV + adjustment factors + security master + trading calendar select the
   exploratory `ashare_market_import_v1` profile;
6. TuShare availability;
7. `DATA_UNAVAILABLE`.

Multiple unaliased releases fail with `RELEASE_SELECTION_REQUIRED`; the program never
guesses which immutable dataset should become current. Absence of data affects research
readiness only—it does not make the process, auth, registry, or configuration unhealthy.

## Release governance

`release import-qlib` freezes an existing provider into an immutable
`ashare_qlib_import_v1` release, verifies hashes, creates a bound DatasetVersion, and
atomically advances the release/dataset aliases. It is exploratory and cannot be used
for Phase 2, Phase 3, TARGET_PORTFOLIO handoff, or Artifact Contract v2 export.

`dataset-build` from governed raw/TuShare inputs publishes
`ashare_qlib_research_v2`, then materializes Qlib and atomically advances both aliases.
Daily sync uses the same full, fail-closed publisher when `publish_on_sync` is enabled.
Every release is self-contained under `<QLIB_DATA_ROOT>/releases/ds_<sha256>/`.
Component bytes are stored once under `releases/objects/<prefix>/<sha256>` and
materialized into each immutable release with hard links. Filesystems without hard-link
support safely fall back to copies. Qlib imports use the same copy-on-write materialization
for their bound DatasetVersion, removing the previous second full provider copy while
preserving ordinary paths and the existing DataRelease/DatasetVersion verification rules.

DatasetVersion and DataRelease verification expose three explicit levels:

- `manifest` validates schema, content identity, semantic bindings and declared inventory
  without opening every payload file; ordinary source resolution uses this level.
- `sampled` deterministically covers the first/last files, every declared directory and a
  SHA-derived fixed sample, checking both size and content SHA-256.
- `deep` reads every declared payload and writes a content-bound receipt under
  `<QLIB_DATA_ROOT>/state/verification_receipts`. Receipt reuse is opt-in with
  `--reuse-receipt`; omitting it always performs the physical deep read.

Legacy migration acceptance is deliberately separate from bootstrap:

```powershell
& $RepoPython -m tushare_qlib migration-acceptance --source qlib `
  --source-root <READ_ONLY_LEGACY_PROVIDER> `
  --acceptance-root <NEW_EMPTY_ACCEPTANCE_ROOT>

& $RepoPython -m tushare_qlib migration-acceptance --source research `
  --source-root <READ_ONLY_LEGACY_DATA_ROOT> `
  --acceptance-root <NEW_EMPTY_ACCEPTANCE_ROOT> `
  --start <YYYY-MM-DD> --end <YYYY-MM-DD> --single-thread
```

The command rejects overlapping or non-empty targets, disables TuShare credentials in the
isolated settings, never runs sync/download commands, deep-verifies the resulting release
and DatasetVersion, and records CAS/hardlink/timing evidence in
`acceptance_evidence.json`. The `qlib` source proves exploratory CAS migration; only the
`research` source publishes `ashare_qlib_research_v2`. Research acceptance fails before
writing unless both qlib-platform and Qlib are clean fixed commits, and it verifies that
the DatasetVersion lineage records those exact commits and the exact DataRelease identity.

When only market inputs are available, `bootstrap --source auto` reports the
certified components that are absent and can still publish
`ashare_market_import_v1`. Its required components are `bars`,
`adjustment_factors`, `security_master`, and `trading_calendar`. The resulting
Qlib DatasetVersion supports `alpha158_market_v1`, formal local training, and
research backtests, but its policies fail closed for Phase 2, Phase 3,
TARGET_PORTFOLIO, research promotion, and Artifact Contract v2 export. Missing even
one market component returns `DATA_INCOMPLETE` with `missingComponents`; bootstrap
never silently invents an adjustment factor, calendar, or security master.

If `--start` and `--end` are omitted, raw and TuShare bootstrap use the configured
`start_date` and `end_date`. Local end-of-day releases record `asOfTime` at
17:30 Asia/Shanghai for their coverage end, rather than midnight UTC.

Capability enforcement is centralized and release-bound. `build-target-portfolio`
and `lean-export` resolve the release from the governed input artifact;
`lean-register` resolves the single release bound by the Artifact v2 bundle; Phase
2 and Phase 3 commands require the active release capability. Portable Phase 3
verification checks the embedded release so cross-machine read-only verification
does not depend on a local current alias.

## Authentication and health

Local multi-user auth uses `<QLIB_DATA_ROOT>/auth/auth.sqlite`, Argon2id password hashes,
RBAC roles (`admin`, `operator`, `researcher`, `viewer`), and a persistent 256-bit signing
key under `<QLIB_DATA_ROOT>/secrets/`. Passwords are entered interactively and are never
accepted as command-line arguments. Local CLI access remains governed by OS/filesystem
permissions and does not call a remote verifier.

The reusable health surfaces map to:

- `health live`: process liveness only;
- `health ready`: configuration, local auth state, registry, and filesystem;
- `health dependencies`: local data, TuShare, platform, and execution-export status.

Platform failure is reported as degraded dependency state, never a research-plane
startup failure. Verified Artifact Contract v2 manifests are copied into a content-addressed
local outbox spool before enqueue, so cleanup of the research output directory cannot remove
pending delivery bytes. Failed delivery stays pending. A one-shot recovery or long-running
retry worker can deliver to an explicitly configured Platform Artifact v2 endpoint:

```powershell
& $RepoPython -m tushare_qlib outbox drain --endpoint https://platform.example/api/artifacts
& $RepoPython -m tushare_qlib outbox worker --endpoint https://platform.example/api/artifacts
```

`PLATFORM_ARTIFACT_ENDPOINT` may replace `--endpoint`; its value is never included in
health output. Each request carries the outbox item id as its idempotency key plus the
artifact SHA-256 and bound DataRelease id. Only a 2xx response acknowledges the item.

`health ready` performs a read-only SQLite quick check and schema check when the Registry
exists, verifies minimum free disk space, and proves a write plus atomic rename on the data
filesystem. Corruption, an incomplete Registry schema, a read-only volume, low disk space,
or rename failure returns `not_ready`; missing market data remains dependency degradation.

## Installation and scheduling

CI builds a wheel, installs it into a fresh virtual environment, and invokes the installed
`tq` entry point through `status -> bootstrap -> train-select -> backtest-predictions ->
research-audit`. This catches editable-install and undeclared-dependency leaks.
A separate `clean-machine-lightgbm` job repeats the installed-wheel path with a bounded
CPU LightGBM profile (`10` boosting rounds and one thread). Both wheel jobs copy only the
acceptance test into a temporary location and run it with the working directory set to an
empty directory, so runtime configs and model profiles must resolve from the installed wheel.

Windows uses `scripts/register_tushare_daily_sync_task.ps1`, whose default is
`configs/pipeline.standalone.yaml`; integrated users must pass
`-ConfigPath configs/pipeline.integrated.yaml`. Linux systemd user units and a macOS launchd
agent can be rendered without installing them automatically:

```bash
.venv/bin/python scripts/render_standalone_scheduler.py \
  --kind systemd --repo-root "$PWD" --python-exe "$PWD/.venv/bin/python" \
  --output-dir /tmp/qlib-systemd

.venv/bin/python scripts/render_standalone_scheduler.py \
  --kind launchd --repo-root "$PWD" --python-exe "$PWD/.venv/bin/python" \
  --output-dir /tmp/qlib-launchd
```

A wheel-only installation also exposes `tq-render-scheduler`; its templates and standalone
configuration are installed with the wheel. For example, from the desired process working
directory:

```bash
tq-render-scheduler \
  --kind systemd --repo-root "$PWD" --python-exe "$VIRTUAL_ENV/bin/python" \
  --output-dir /tmp/qlib-systemd
```

The systemd timer binds `18:30` explicitly to `Asia/Shanghai`; launchd follows the macOS
host timezone, which must be configured to `Asia/Shanghai` for the same wall-clock behavior.

Review the rendered files before copying them into the user service directory and enabling
them. Rendering and tests never register or start a scheduled job.

## Isolation acceptance

The `standalone-isolation` CI job unsets Platform/TuShare data dependencies and uses a
synthetic A-share Qlib provider to run the project CLI itself:

```text
release import-qlib
  -> train-select --stage signal
  -> immutable PredictionSnapshot
  -> backtest-predictions
  -> research-audit
```

The test asserts the ResearchExperiment and PredictionSnapshot remain bound to the
imported immutable DataRelease. It also proves local auth, no-data startup, release
resolution, capability rejection, and outbox outage/recovery behavior.
