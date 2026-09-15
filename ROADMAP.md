# qlib-platform Roadmap

> Updated: 2026-09-15  
> Audit baseline: `main@3cbc0d98457bb4c49befdac9560dd97355e1fcc0`  
> Round-2 tracking epic: [#128](https://github.com/magic-alt/qlib-platform/issues/128)

## North Star

`qlib-platform` should remain an upstream-first Qlib research engine while adding the contracts required for a trustworthy A-share research platform:

```text
Provider data (Tushare / other adapters)
        │
        ▼
immutable raw/normalized release
        │
        ├── point-in-time calendar / universe / adjustment semantics
        ▼
Qlib-native research view
        │
        ├── official-parity benchmark lane
        ├── enhanced Alpha/PIT research lane
        └── model / experiment matrix
        ▼
reproducible predictions + research backtest
        │
        ▼
quality / statistical / market-realism gates
        │
        ▼
versioned research artifacts
        │
        └── governed handoff to an execution platform when separately approved
```

The goal is **not** to fork or replace Qlib. Platform extensions must stay in `qlib_platform.*`, keep upstream-native behavior available, and make every deviation explicit and testable.

## Current Baseline After Round 1

The first audit (#104–#110) established the major platform abstractions. Round 2 therefore starts from a stronger baseline than a typical research prototype.

### Already present

- pinned `pyqlib==0.9.7` with a Qlib compatibility manifest and CI capability checks;
- real `qlib.cli.run` versus platform-entry conformance using Alpha158 + LightGBM on a deterministic synthetic provider;
- Tushare daily synchronization with retry, overlap refresh, locking, run ledger and reports;
- bronze/silver data paths, Qlib export/DataView validation and release/provenance support;
- point-in-time universe support and dataset/release identities;
- ResearchProfile / AlphaPack / model / strategy abstractions;
- artifact contracts and cross-repository handoff boundaries;
- a daily signal runner capable of sync → feature build → prediction → portfolio intent;
- systemd/launchd scheduling foundations;
- CI, documentation, release and security workflows.

### Not yet certified

- a production end-to-end scheduled DAG from data freshness through immutable release to daily regression/backtest/report;
- market-data parity between Tushare-built Qlib data and the official Qlib Alpha158/LightGBM CSI300 benchmark;
- a quantified claim of full Qlib upstream compatibility beyond the currently declared/tested surface;
- strict production-only configuration that rejects test/coverage/mutable-release shortcuts;
- comprehensive historical A-share trading-rule conformance;
- one cross-layer run identity that reproduces data → features → model → prediction → backtest;
- production SLO/fault-injection/game-day evidence;
- institutional multi-user control-plane capabilities.

## Key Audit Decisions

### 1. Daily data synchronization exists; daily production research is not the same thing

`daily-sync` is already substantial and the systemd reference timer runs at 18:30 Asia/Shanghai. Round 2 does not replace it. The missing certification is the fail-closed dependency chain:

`calendar → sync → quality → immutable release → Qlib view → research/backtest → report/archive`.

A stale or partial provider response must never silently become a successful daily backtest or signal run.

### 2. “Supports Qlib” must be measurable

The repository pins Qlib and already tests important real upstream behavior. The roadmap distinguishes four states:

- **upstream-native / pass-through** — official Qlib path is used directly;
- **certified parity** — behavior is covered by conformance evidence;
- **platform extension** — extra behavior under `qlib_platform.*`;
- **unsupported / not certified** — upstream surface exists but no compatibility promise is made.

A broad “fully inherits every Qlib capability” claim is allowed only when a machine-readable capability matrix and its tests support that claim for a specific Qlib version.

### 3. The Tushare research profile is not the official Alpha158 benchmark

The current Tushare development profile intentionally differs from the official Qlib benchmark: it includes PIT-enhanced features, a different label horizon, different date/split logic and different portfolio policy. That lane is useful research, but it must not be used to answer whether the official example has been reproduced.

A separate frozen `official-parity` profile will match the official workflow protocol before any result comparison is made.

Official workflow reference:
<https://github.com/microsoft/qlib/blob/main/examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml>

Official benchmark table:
<https://github.com/microsoft/qlib/blob/main/examples/benchmarks/README.md>

Current LightGBM / Alpha158 / CSI300 benchmark table reference:

| Metric | Official reference |
| --- | ---: |
| IC | 0.0448 |
| ICIR | 0.3660 |
| Rank IC | 0.0469 |
| Rank ICIR | 0.3877 |
| Annualized return | 0.0901 |
| Information ratio | 1.0164 |
| Max drawdown | -0.1038 |

These are **reference statistics, not exact-value requirements for a different vendor dataset**. Tushare parity will use pre-registered tolerances plus attribution for universe, normalization, revision and market-data differences. The existing `min_icir: 0.5` is therefore not itself an “official benchmark replication” gate.

## Round-2 Workstreams

### P0 — Close the production and benchmark loop

#### [#129 Daily Production DAG](https://github.com/magic-alt/qlib-platform/issues/129)

Build one fail-closed, resumable orchestration contract for:

- exchange-calendar-aware session selection;
- provider freshness and endpoint-specific coverage gates;
- immutable release publication and atomic activation;
- Qlib view/materialization verification;
- frozen reference research/backtest regression;
- reporting, notifications and archival;
- idempotency, leases, crash recovery and backfill;
- a single canonical schedule source for systemd/launchd/future schedulers.

**Exit gate:** partial/stale data cannot produce a successful downstream run; fault injection demonstrates safe resume at every critical node.

#### [#130 Tushare Pro × official Alpha158 parity](https://github.com/magic-alt/qlib-platform/issues/130)

Create a frozen profile matching the official CSI300 + Alpha158 + LightGBM protocol, including:

- exact Alpha158 handler semantics;
- official train/valid/test windows;
- official LightGBM parameters;
- TopkDropout `topk=50`, `n_drop=5`;
- official benchmark cost/fill assumptions;
- PIT CSI300 membership, trading calendar and adjustment semantics built from Tushare;
- engine parity and data-vendor parity reported separately.

**Exit gate:** one documented command produces a machine-readable parity report with reference value, local value, delta, tolerance, PASS/FAIL and attribution for every benchmark metric.

#### [#131 Qlib Upstream Compatibility](https://github.com/magic-alt/qlib-platform/issues/131)

Turn compatibility into a versioned capability matrix and upgrade gate.

**Exit gate:** every certified Qlib capability maps to a test; unsupported/optional paths are explicit; new upstream versions cannot change the compatibility claim without evidence.

## P1 — Make research evidence trustworthy and operable

#### [#132 Production Config Ratchet](https://github.com/magic-alt/qlib-platform/issues/132)

Separate `dev`, `ci`, `benchmark` and `prod` policy. Production must reject test-only coverage modes, in-place release mutation and required-quality bypasses before data is written.

#### [#133 A-share Market Realism Conformance](https://github.com/magic-alt/qlib-platform/issues/133)

Version and test historical market rules: T+1 sellability, price-limit regimes, suspension/tradability, board-lot/odd-lot handling, fees/taxes, corporate actions and listing lifecycle.

Official-parity assumptions and production-realism assumptions remain separate so a more realistic simulator does not accidentally invalidate benchmark comparison.

#### [#134 End-to-End Research Lineage](https://github.com/magic-alt/qlib-platform/issues/134)

Define one canonical run manifest containing the immutable data release, universe, calendar, feature pack, label, split, code/config/environment, model/seed, prediction, policy, market rules, costs and output digests.

**Exit gate:** a run can be inspected and reproduced from `run_id`; missing/tampered inputs fail verification instead of falling back to `latest`.

#### [#135 Research Platform SRE](https://github.com/magic-alt/qlib-platform/issues/135)

Define data/research/platform SLI/SLO, actionable alerts, incident correlation, persistent recovery and game-day fault injection.

**Exit gate:** provider lateness, schema drift, partial endpoints, corrupt export, disk pressure, host restart and missed schedules have tested fail-closed/recovery behavior.

## P2 — Institutional control plane and scale

#### [#136 Institutional Control Plane](https://github.com/magic-alt/qlib-platform/issues/136)

Keep the Qlib research engine lightweight and independently runnable, while adding optional institutional services around it:

- project/user/service identity and RBAC;
- secret-provider references and data-entitlement metadata;
- object-store/artifact and metadata-store abstractions;
- append-only audit and approval events;
- local/worker/batch executor contract with idempotency, cancellation and resume;
- resource budgets, concurrency and experiment governance.

**Exit gate:** local filesystem/local executor remains a first-class offline mode; enabling the control plane is optional and does not duplicate Qlib or the execution platform.

## Institutional Readiness Gates

The repository should not describe itself as an institutional-grade research platform until all mandatory gates below are green.

| Gate | Requirement | Primary issue |
| --- | --- | --- |
| G0 Data Reliability | exchange-calendar-aware freshness/coverage/quality, immutable release, rollback | #129, #132 |
| G1 Upstream Parity | declared Qlib surface has versioned conformance evidence | #131 |
| G2 Benchmark Parity | Tushare official Alpha158 lane reaches pre-registered parity tolerances | #130 |
| G3 Reproducibility | any accepted run is traceable/replayable from immutable identity | #134 |
| G4 Market Realism | historical A-share rule/cost fixtures pass | #133 |
| G5 Daily Operations | DAG, SLO, alerting, crash recovery and backfill game-day pass | #129, #135 |
| G6 Governance | RBAC, secrets, audit, artifact governance and executor isolation pass | #136 |

## Definition of a Complete A-share Research Flow

A production A-share study is complete only when every stage has a versioned input/output identity:

1. provider request and entitlement;
2. raw response / bronze object;
3. normalized canonical data;
4. exchange calendar and point-in-time universe;
5. immutable provider/data release;
6. Qlib-native view validation;
7. AlphaPack/feature materialization;
8. label and information cutoff;
9. split/fold and leakage controls;
10. model training and deterministic seed/config;
11. prediction artifact;
12. signal/portfolio policy;
13. market-rule/cost/fill model;
14. research backtest and statistical diagnostics;
15. gate decision and report;
16. optional governed artifact promotion/handoff.

A failure or unknown state at an earlier required stage blocks later stages. A profitable backtest is not permission to bypass a failed data, leakage, reproducibility or governance gate.

## Architecture Principles

1. **Upstream-first Qlib** — reuse official handlers/models/workflows/backtest wherever possible.
2. **No hidden monkey patches** — local behavior lives in versioned platform adapters/extensions.
3. **Point-in-time by construction** — universe, fundamentals, corporate actions and information availability carry as-of semantics.
4. **Immutable research inputs** — mutable aliases resolve to immutable identities before execution.
5. **Fail closed in production** — missing, stale, unauthorized or ambiguous required data blocks the run.
6. **Benchmark lane != enhanced research lane** — official parity is frozen; enhancements get new profile IDs.
7. **Research != execution authority** — qlib-platform produces research artifacts; broker/OMS/live risk remain outside the research engine.
8. **Offline first, control plane optional** — personal/local research remains fully supported as institutional services are added.
9. **Failures are data** — failed/rejected trials and incidents are retained and traceable.
10. **No result-driven gate editing** — thresholds/tolerances are versioned before observing the result they judge.

## Recommended Implementation Order

```text
#129 Daily DAG ─────────────┐
                           ├── #132 Production config ──┐
#131 Qlib compatibility ───┤                            ├── #135 SRE
                           │                            │
#130 Official parity ──────┼── #133 Market realism     │
                           │                            │
                           └── #134 Run lineage ────────┘
                                                        │
                                                        ▼
                                               #136 Control plane
```

#130 and #131 should start early because they establish the external reference contract. #132/#134 then make the pipeline safe and reproducible; #133/#135 certify market and operational realism; #136 scales the already-stable contracts rather than inventing them inside a distributed system.

## Out of Scope for This Roadmap

- declaring a strategy profitable or suitable for live trading;
- automatically unsealing final holdouts;
- auto-selecting or auto-promoting models based on daily monitoring;
- replacing Qlib with a platform-specific model/backtest engine;
- moving broker/OMS/live risk controls into the research repository;
- requiring exact numeric equality between Qlib's bundled/community data and Tushare where vendor history differs.

## Tracking

Round-2 epic and task list: [#128](https://github.com/magic-alt/qlib-platform/issues/128).

When a task closes, its PR should update this roadmap's gate status only when the acceptance evidence is present; merging implementation code alone is not sufficient to mark an institutional readiness gate complete.
