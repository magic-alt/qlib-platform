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
4. local raw market data requiring a full local release build;
5. TuShare availability;
6. `DATA_UNAVAILABLE`.

Multiple unaliased releases fail with `RELEASE_SELECTION_REQUIRED`; the program never
guesses which immutable dataset should become current. Absence of data affects research
readiness only—it does not make the process, auth, registry, or configuration unhealthy.

## Release governance

`release import-qlib` copies an existing provider into an immutable
`ashare_qlib_import_v1` release, verifies hashes, creates a bound DatasetVersion, and
atomically advances the release/dataset aliases. It is exploratory and cannot be used
for Phase 2, Phase 3, TARGET_PORTFOLIO handoff, or Artifact Contract v2 export.

`dataset-build` from governed raw/TuShare inputs publishes
`ashare_qlib_research_v2`, then materializes Qlib and atomically advances both aliases.
Daily sync uses the same full, fail-closed publisher when `publish_on_sync` is enabled.
Every release is self-contained under `<QLIB_DATA_ROOT>/releases/ds_<sha256>/`.

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
startup failure. Verified Artifact Contract v2 manifests are placed in a durable local
outbox. Failed delivery keeps them pending; recovery can acknowledge them without a
qlib-platform restart.

## Isolation acceptance

The `standalone-isolation` CI job unsets all Platform and TuShare variables and uses a
synthetic A-share Qlib provider to run immutable import, feature query, minimal training,
and an OOS portfolio-return smoke test. It also proves local auth, no-data startup,
release resolution, and outbox outage/recovery behavior.
