---
status: ACTIVE
owner: architecture
applies_to_commit: 8692afefe1f6cc82ab1f276fca788888f9f30f3e
last_verified: 2026-08-28
---

# Architecture

```text
immutable DataRelease
        |
        v
DatasetVersion -> FeatureSnapshot -> model / PredictionSnapshot
        |                                  |
        +---------------- research --------+
                                           v
                                 PortfolioPolicy
                                           |
                                           v
                                  TARGET_PORTFOLIO
                                           |
                                  Artifact Contract v2
                                           |
                                           v
                                        platform
```

The Research Plane owns immutable research data materialization, PIT features, folds, models, research
backtests, target construction and research promotion. The Execution Plane owns authoritative LEAN
semantics, hard risk, OMS, broker/QMT, orders, fills and ledger.

Read [Architecture Boundary](architecture_boundary.md) for the normative ownership contract and
[Identity and Lineage](identity_and_lineage.md) for identity/hash details.
