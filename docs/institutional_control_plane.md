---
status: ACTIVE
owner: architecture
applies_to_commit: df8d5f3782842bd38bc36d6c908d9c0207f81fc3
last_verified: 2026-09-17
---

# Institutional control plane

Issue #136 adds an **optional** institutional control plane around the existing research engine. It does not replace Qlib and it is not required for standalone research.

## Three-plane boundary

```text
Control plane
  project membership / RBAC / approval / audit / secret refs
  artifact metadata / entitlement / quota / retention
            |
            | immutable ExecutionRequest
            v
Execution backend
  LocalExecutionBackend (golden reference)
  FakeWorkerExecutionBackend (queue/worker contract fixture)
  future batch/container/worker adapters
            |
            | versioned descriptor/config/artifact refs
            v
Research engine
  qlib-platform CLI/library -> Qlib datasets/models/backtests
```

The control plane owns **who may request work and which immutable identities are visible**. The execution backend owns **delivery and lifecycle mechanics**. The research engine continues to own scientific semantics and may still run entirely locally.

## Identity and duplicate delivery

`ExecutionRequest.business_id` is SHA-256 over canonical, business-relevant request fields:

- project identity;
- versioned operation;
- descriptor/config references;
- immutable artifact references;
- secret **references** (never values);
- declared resource budget and matrix cardinality.

Worker delivery IDs, process IDs, retry counters and scheduler-specific fields are deliberately excluded. Therefore the same request has the same business identity on local and worker backends, while a duplicate worker delivery returns the existing terminal record instead of executing the scientific run again.

`cancel` and `resume` are idempotent lifecycle operations. Resume does not invent a new business identity. A failed or cancelled request may be retried under the same identity with a new attempt count.

## Project isolation and RBAC

Project roles are:

| Role | Research-plane intent | Institutional action intent |
| --- | --- | --- |
| `viewer` | read | none |
| `researcher` | read/write/run | none |
| `operator` | read/run | cancel/resume execution |
| `approver` | read | governed high-risk approvals |
| `maintainer` | read/write/run/share | research administration |
| `owner` | full research project administration | project ownership |
| global `admin` | administrative override | explicit control-plane administrative authority |

High-risk operations are **not** derived from ordinary research CRUD grants. Release activation, benchmark-threshold changes, model/artifact promotion and manual overrides require the dedicated institutional action policy plus an immutable approval reference and a reason. Denied and successful attempts are both written to the tamper-evident audit log.

## Secrets

Runs persist `SecretRef(provider, project_id, name, version)`, never the resolved value. Providers are replaceable:

- `EnvSecretProvider` for explicit environment bindings;
- `KeyringSecretProvider` for optional local desktop/keyring use;
- `MemorySecretProvider` for tests/reference behavior;
- future Vault/cloud secret-manager adapters can implement the same protocol.

Every provider verifies `ref.project_id == requesting project_id`; cross-project resolution fails closed.

## Artifact and metadata backends

`ObjectStore` keeps large bytes outside metadata databases. The reference implementations are:

- `FileSystemObjectStore`: standalone/offline content-addressed storage;
- `MemoryObjectStore`: cloud-neutral contract fixture.

Objects are addressed by SHA-256 and scoped by project. Filesystem reads reject cross-project refs, traversal and symlink object targets.

`MetadataStore` contains only identity/state/index information. `MemoryMetadataStore` and `SqliteMetadataStore` share the same register/get/reference-counting contract. Immutable metadata includes the object digest, manifest digest, optional entitlement reference, retention timestamp and reference count; payload bytes are never inserted into SQLite.

## Entitlements

`DataEntitlement` records provider/license identity, allowed projects and whether export is permitted. Artifact/release metadata carries the entitlement ID. Export and use checks are explicit and fail closed for unknown entitlements, unlicensed projects or licenses that forbid export.

This is intentionally separate from research performance metadata: licensing facts must not be inferred from a model or backtest result.

## Resource governance

Each `ExecutionRequest` declares CPU, memory, runtime and experiment-matrix cardinality. `ProjectQuota` checks:

- per-project active concurrency;
- maximum matrix jobs;
- CPU-hours per run;
- memory budget;
- runtime budget.

The contract reuses the existing research workflow/ExperimentMatrix semantics: it limits and dispatches immutable research requests; it does not create another hyper-parameter-search engine.

## Security boundaries

The envelope has no raw `shell`, `command`, `python`, `script`, `code` or generic executable payload field. Web/API layers must submit only versioned descriptor/config/artifact references that the research engine knows how to resolve.

The research control plane does not hold broker credentials and does not gain OMS/live-order permissions. Live trading remains the responsibility of `lean-local-platform` or another dedicated execution system.

## Offline compatibility

Nothing in `qlib_platform.control_plane` is imported by the normal standalone quickstart path. Filesystem artifacts, local Qlib research, systemd/launchd scheduling and direct CLI/library use continue to work without configuring users, secrets providers, an object service or a worker queue.

## Contract certification

`tests/test_institutional_control_plane.py` verifies:

- project-role isolation and fail-closed cross-project access;
- secret-reference isolation and non-persistence of secret values;
- filesystem/memory object-store parity;
- SQLite/memory metadata-store parity and reference counting;
- path-traversal/symlink boundaries;
- identical local/worker business identity and artifact identity;
- duplicate-delivery idempotency;
- deterministic cancel/resume semantics;
- resource-budget enforcement;
- high-risk RBAC + approval + tamper-evident audit;
- entitlement use/export boundaries;
- absence of arbitrary executable payload fields.
