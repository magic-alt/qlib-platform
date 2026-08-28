---
status: ACTIVE
owner: operations
applies_to_commit: 8692afefe1f6cc82ab1f276fca788888f9f30f3e
last_verified: 2026-08-28
---

# Troubleshooting

## Repository-local interpreter missing

Windows standard `venv` layout requires `.venv\Scripts\python.exe`. If it is absent, stop and
recreate the repository-local environment; do not substitute system Python.

## DataRelease passed to a dataset command

`release verify <DATA_RELEASE_REF>` validates DataRelease. `dataset-resolve/show/verify` and
`live-inference --dataset-ref` require DatasetVersion ID/alias. Resolve or materialize the dataset
first; never relabel one ID as the other.

## qrun provider path fails on another machine

Set `QLIB_DATA_URI` to the immutable path returned by `dataset-resolve`. Supported workflows use
`{{ QLIB_DATA_URI }}`; a workstation absolute path is a documentation/configuration defect.

## LightGBM OpenCL build changes dependency version

The project pin is `lightgbm==4.6.0`. The Windows build script must compile that exact version. A
different GPU version requires a separately reviewed runtime profile and dependency update.

## Phase 3 command refuses a dirty checkout

This is expected. Phase 3 locks a clean source revision and implementation hashes. Do not weaken the
check or alter governed windows. Also remember that `phase3-diagnose` writes immutable evidence and is
not a generic smoke test.

## Platform unavailable

Research startup remains independent. Verified bundles stay in the durable outbox until acknowledged.
Do not bypass checksum/lineage validation or delete delivery state to make health checks pass.

## Credential/config issue

Report only variable names, presence/absence, length or expected format. Never print, paste, screenshot,
log or upload a credential value or `.env` content.
