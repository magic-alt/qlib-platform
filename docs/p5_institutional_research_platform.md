---
status: ACTIVE
owner: architecture
applies_to_commit: d0faf1120c11baefdb6b6921590fcd56f432e442
last_verified: 2026-09-06
---

# P5 — Institutional Research Platform

P5 is an additive institutionalization program on top of the P0–P4 repository baseline. It does
not replace Qlib, the existing research lifecycle, Artifact Contract v2, or the explicit boundary
between `qlib-platform` research and the separate execution platform.

The agreed order is fixed:

1. **P5-A Risk Platform**
2. **P5-B Portfolio Construction**
3. **P5-C Execution Research**
4. **P5-D Enterprise Research Management**

## P5-A — Risk Platform

P5-A turns the existing covariance/factor-risk primitives into a benchmark-aware portfolio risk
surface suitable for institutional research.

Implemented capability:

- absolute portfolio variance/volatility;
- benchmark-relative active weights and tracking error;
- Euler marginal risk contribution (MCR) and component risk contribution;
- leave-one-position-out incremental risk contribution (ICR);
- factor versus specific risk decomposition using the existing Barra-like model;
- benchmark-relative factor risk by applying the same decomposition to active weights;
- deterministic factor/asset scenario stress testing;
- fail-closed instrument/factor alignment, finite-value, symmetry and PSD validation;
- dedicated deterministic tests and CI contract.

P5-A was merged by PR #99 at `0b88ee912d5a5ef9135b5113a32d886e9da1e0a6` after the full
repository CI, P5 risk contract, Qlib capability contract, macOS, dependency review, CodeQL, docs
and release checks completed successfully.

P5-A is research infrastructure only. It does not create candidates, select models, use the final
holdout, publish research artifacts, or enforce live hard-risk limits.

## P5-B — Portfolio Construction

P5-B extends the existing optimizer rather than creating a parallel optimizer stack. Implemented
capability includes:

- existing alpha/risk mean-variance objective preserved as the default profile;
- benchmark-relative alpha/active-risk objective;
- explicit tracking-error budgets;
- minimum-variance portfolios;
- risk-parity portfolios with explicit risk budgets;
- robust alpha uncertainty haircut and covariance diagonal buffering;
- turnover and total transaction-cost budgets;
- absolute and benchmark-relative sector/factor exposure constraints;
- cardinality and minimum-position constraints with fail-closed feasibility checks;
- A-share round-lot implementation from continuous weights using price/NAV snapshots;
- continuous target weights retained separately from executable target shares/weights;
- deterministic feasibility, alignment and regression tests.

The existing `OptimizationConstraints`, `OptimizationConfig`, `optimize_alpha_portfolio` and
`optimized_target_portfolio` remain the migration path. P5-B adds capability to those APIs rather
than introducing a second portfolio-construction framework.

The round-lot layer is deliberately downstream of continuous optimization. Share-lot sizing is not
mathematically meaningful without a portfolio NAV and price snapshot, and therefore is represented
as an implementation transform rather than a fake continuous weight constraint.

P5-B was merged by PR #100 at `d0faf1120c11baefdb6b6921590fcd56f432e442`. Its final immutable
PR head passed the P5-A risk contract, P5-B portfolio contract, full repository CI including Linux,
Windows and coverage, Qlib capability contract, macOS, dependency review, CodeQL, docs and release
checks. No typing or quality gate was weakened.

## P5-C — Execution Research

P5-C is **execution research**, not OMS ownership. The active implementation scope is:

- intraday bar research contracts and arrival/VWAP/TWAP/end benchmarks;
- deterministic VWAP, TWAP and POV schedule research;
- queue/capacity expected-fill and partial-fill models;
- spread/market-impact and latency assumptions;
- cancel/reject and externally supplied broker-event analytics;
- implementation-shortfall and arrival/VWAP attribution;
- reconciliation against the existing certified research portfolio accounting audit.

The execution-research implementation lives in the responsibility-oriented
`qlib_platform.research.execution` package. It reuses `backtesting.execution_audit` for accounting
reconciliation instead of creating another execution ledger or accounting engine.

Order submission, cancellation, replacement, broker-state writes, execution ledgers and hard-risk
enforcement remain out of this repository and belong to the execution platform. Research fills are
expected/deterministic simulation evidence, not broker commands or authoritative exchange fills.

## P5-D — Enterprise Research Management

P5-D will institutionalize multi-user research governance and access management around the existing
ExperimentStore, evidence and artifact systems. Planned capabilities include:

- OIDC/OAuth2 integration surfaces;
- SSO and LDAP/Active Directory adapters;
- role-based and resource-scoped access control;
- immutable audit events for research/admin actions;
- service-account and API-token lifecycle contracts;
- experiment/project ownership and authorization boundaries;
- policy-driven access to governed data and research artifacts.

Authentication/authorization must not be confused with model promotion authorization or execution
permissions.

## Cross-cutting acceptance rules

Every P5 workstream must:

- extend existing responsibility-oriented packages rather than introduce phase-numbered runtime
  modules;
- preserve PIT causality, fold isolation, OOS stitching, content-addressed identities and sealed
  final-holdout behavior;
- fail closed on ambiguous alignment, lineage, constraints or permissions;
- add deterministic unit/contract tests and a CI-visible acceptance surface;
- preserve the single `TARGET_PORTFOLIO` cross-repository handoff;
- avoid broker/OMS authority and authoritative LEAN execution semantics;
- document what is and is not certified before the workstream is declared complete.

## Current status

- P0–P4 repository baseline: **REVALIDATED** at
  `a74e568b0f1660da9bbbc6ed8ff6203c001f1e58`.
- P5-A: **COMPLETE / MERGED** at `0b88ee912d5a5ef9135b5113a32d886e9da1e0a6`.
- P5-B: **COMPLETE / MERGED** at `d0faf1120c11baefdb6b6921590fcd56f432e442`.
- P5-C: **IN PROGRESS**.
- P5-D: **NOT STARTED**.
- Active research program remains Phase 3-D diagnosis-only; P5 does not change research
  authorization state.
