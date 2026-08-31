---
status: ACTIVE
owner: architecture
applies_to_commit: 4f3f4369b6e55186967bc726bb8dd87fff0e5d70
last_verified: 2026-08-31
---

# Glossary

- **Research Plane** — this repository's domain: immutable research data, features, models, research backtests, target construction, local model/signal operations and research artifact publication.
- **Execution Plane** — `magic-alt/platform` domain: authoritative LEAN semantics, hard risk, OMS, broker/QMT, orders, fills, positions and ledger.
- **DataRelease** — immutable upstream fact release with component/file identities. A DataRelease ID is not a DatasetVersion reference.
- **DatasetVersion** — immutable Qlib dataset materialized from data inputs and bound through semantic lineage to its parents. Research/inference `--dataset-ref` consumes a DatasetVersion ID/alias.
- **Dataset alias** — mutable registry name (for example `research-current`) pointing to an immutable DatasetVersion. Resolve/pin it before a governed run.
- **FeatureSnapshot** — immutable feature partitions and fitted recipe contract.
- **PredictionSnapshot** — immutable score/label payload plus model, fold and upstream research contract.
- **RealizedLabelSnapshot** — immutable matured-outcome evidence bound to DataRelease, LabelSpec, source artifact and pinned calendar/observation cut.
- **PredictionEvaluationSnapshot** — immutable monitoring evidence joining one verified PredictionSnapshot to a compatible RealizedLabelSnapshot; not a promotion trigger.
- **AlphaPack** — versioned feature-family definition.
- **LabelSpec** — causal label definition, horizon and timing.
- **SplitSpec** — fold, purge, embargo and holdout definition.
- **ModelRelease** — governed fitted model artifact and runtime lineage.
- **Local deployment** — ModelRegistry selection used by local inference. It is not `platform` production approval/deployment state.
- **MODEL_TOPK** — ranked model candidates/scores; not a target portfolio and not a cross-repository execution handoff.
- **Research-backtest strategy policy** — stateful simulated Qlib strategy such as TopkDropout or RankBuffer. Historical manifest fields may call this `execution.strategyPolicy`, but it remains research simulation.
- **PortfolioPolicy** — target-weight construction policy applying weighting, position/exposure/group/turnover caps before handoff.
- **TARGET_PORTFOLIO** — immutable target weights; the sole execution-semantic qlib-to-platform handoff.
- **Artifact Contract v2** — five-node research publication graph bound to one DataRelease.
- **Outbox** — durable local queue for already-exported immutable Artifact v2 bundles awaiting delivery/acknowledgement.
- **Verification: manifest** — verify manifest/schema/identity/inventory without reading every payload byte.
- **Verification: sampled** — deterministic bounded payload verification.
- **Verification: deep** — verify every declared payload and emit verification evidence; used by governed high-assurance paths.
- **RESEARCH_PROMOTED** — maximum lifecycle state owned by this repository. It is not `LEAN_VALIDATED`, `PAPER` or `PRODUCTION`.
- **Final holdout** — sealed evaluation period prohibited from selection and tuning until its governed access conditions are met.
- **PIT** — point-in-time visibility; a value may be used only after its causal availability time.
- **Fail closed** — reject missing, ambiguous or mismatched identity/lineage instead of guessing, silently degrading or editing evidence to pass.
- **Fail soft** — allow optional external availability to degrade without converting it into an integrity bypass; for example, platform delivery can be unavailable while local verified research remains usable.
