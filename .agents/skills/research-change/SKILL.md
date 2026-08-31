---
name: research-change
description: Implement or review qlib-platform alpha, model, label, walk-forward, regime, cache, or research-configuration changes while preserving PIT, fold, OOS, and holdout invariants.
---

# Governed research change

Before changing behavior, identify the research phase, input and output artifact identities, state transition, and final-holdout implications. Read the closest `AGENTS.md` and the governing research documentation/configuration for the altered subsystem.

Preserve these non-negotiable properties:

- point-in-time feature and label causality, including announcement timing, purge, embargo, and label-lookahead gaps;
- fitted-state isolation per fold and strictly ordered, non-overlapping OOS prediction stitching;
- immutable DataRelease, DatasetVersion, FeatureSnapshot, AlphaPack, label, split, PredictionSnapshot, and portfolio identities;
- deterministic cache keys, lineage, hashes, checkpoint publication, and resume behavior;
- sealed final holdout: it must not influence feature, model, hyperparameter, portfolio, or promotion selection;
- fail-closed validation when identity, timing, contract, checksum, or state cannot be proven.

Treat weak IC, IR, net return, or a rejected research gate as research evidence, not a reason to silently change certified infrastructure semantics. Changes to temporal or selection semantics require explicit leakage/holdout tests. Keep a research change on a task branch and document changed definitions, configurations, and validation evidence in the PR.

For diagnostic-only work, use `research-diagnostics`. If a diagnostic requires a new fitted state or changes research identity, apply both skills. The Phase 3-D profile is routed from `research-diagnostics`.
