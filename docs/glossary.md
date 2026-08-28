---
status: ACTIVE
owner: architecture
applies_to_commit: 8692afefe1f6cc82ab1f276fca788888f9f30f3e
last_verified: 2026-08-28
---

# Glossary

- **DataRelease** — immutable upstream fact release with component/file identities.
- **DatasetVersion** — immutable Qlib dataset materialized from data inputs; may bind one DataRelease.
- **FeatureSnapshot** — immutable feature partitions and their fitted recipe contract.
- **PredictionSnapshot** — immutable score/label payload plus model, fold and upstream research contract.
- **AlphaPack** — versioned feature family definition.
- **LabelSpec** — causal label definition, horizon and timing.
- **SplitSpec** — fold, purge, embargo and holdout definition.
- **ModelRelease** — governed fitted model artifact and runtime lineage.
- **MODEL_TOPK** — ranked model candidates; not a target portfolio and not an execution handoff.
- **Backtest execution policy** — Qlib stateful simulation policy such as TopkDropout or RankBuffer.
- **PortfolioPolicy** — target-weight construction policy applying weighting, position/exposure and turnover caps.
- **TARGET_PORTFOLIO** — immutable target weights; the sole execution-semantic qlib-to-platform handoff.
- **Artifact Contract v2** — five-node research publication graph bound to one DataRelease.
- **Final holdout** — sealed evaluation period prohibited from selection and tuning.
- **PIT** — point-in-time visibility; a value may be used only after its causal availability time.
- **Fail closed** — reject missing, ambiguous or mismatched identity/lineage instead of guessing or repairing.
