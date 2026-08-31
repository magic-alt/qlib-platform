---
status: FROZEN
owner: architecture
applies_to_commit: 4f5c5d5
last_verified: 2026-08-31
---

# Research Infrastructure Certification

## Decision

As of 2026-08-17, the research infrastructure baseline is:

> **Research Infrastructure baseline: CERTIFIED @ `4f5c5d5`**

This decision freezes the infrastructure-validation phase and makes Alpha, model, regime and portfolio research the default explanation space for weak strategy results. A low IC, unstable IR, failed Research Gate or poor net return is not, by itself, evidence of data leakage, fold stitching errors, cache contamination, account resets, checkpoint corruption or model-switching failure.

The certified code baseline is commit `4f5c5d5` on `main`. The acceptance protocol and required evidence are defined in [Full Walk-forward Acceptance](full_walk_forward_acceptance.md).

## Post-certification interpretation

This document is a **frozen certificate**. It intentionally does not embed a moving “current main SHA”, commit-distance count, active research phase or current revalidation status.

For those changing facts, use [Current State](current_state.md).

The `CERTIFIED` claim applies only to the code/scope frozen at `4f5c5d5`. Post-baseline standalone release/capability work, Artifact v2 changes, portfolio-policy changes, production-feedback work, Phase 3 implementation or any later change does not automatically inherit certification.

## Certified scope

The certification covers these invariants under the governed contracts:

- immutable DataRelease, FeatureSnapshot, AlphaPack, label, split and portfolio identities;
- point-in-time feature and label causality, including purge, embargo and label-lookahead gaps;
- isolated per-fold fitted state and strictly ordered, non-overlapping OOS prediction stitching;
- reuse of a governed feature cache without raw rematerialization during acceptance;
- one continuous rolling-OOS portfolio account without fold-boundary state resets;
- atomic checkpoint publication, validated resume, corruption invalidation and selective rebuild;
- byte-exact replay between uninterrupted and resumed runs for governed artifacts;
- final-holdout isolation behind a research selection lock and non-publishing evaluation;
- model-adapter switching without changing data, AlphaPack, label, split or portfolio contract;
- separation of system acceptance from Research Quality outcomes.

The certification records infrastructure correctness, not Alpha quality or production readiness. It does not assert that `alpha158_pit_v1`, Ridge, LightGBM, XGBoost or the current portfolio policy has sufficient predictive/economic value.

## Default attribution policy

After certification, investigate weak performance in this order:

1. feature efficacy and stability;
2. regime dependence and sampling variation;
3. model fit, interactions and regularization;
4. portfolio construction, turnover, capacity and costs;
5. infrastructure only when a certified invariant produces contradictory evidence.

Do not reopen infrastructure diagnosis solely because a model is `REJECTED`, remains `RESEARCH_REVIEW`, or underperforms another model on the same snapshot.

## Conditions that reopen certification

Certification must be re-examined when at least one condition is observed:

- a governed identity or payload checksum changes without a new version;
- a PIT, purge, embargo, label-alignment or fold-overlap assertion fails;
- baseline and resumed runs cease to be exact under the acceptance comparator;
- a corrupt/incomplete checkpoint is reused;
- rolling portfolio state resets/diverges at a fold boundary;
- final holdout influences model, feature, hyperparameter or portfolio selection;
- changing only the model profile mutates a supposedly fixed research contract;
- a material change is made to data processing, feature semantics, split construction, caching, checkpointing, prediction snapshots, portfolio accounting or acceptance logic.

When none is present, infrastructure speculation should not displace research diagnosis.

## Current work

Do not infer the active program from this frozen certificate. Follow [Current State](current_state.md) and the active documentation index instead.
