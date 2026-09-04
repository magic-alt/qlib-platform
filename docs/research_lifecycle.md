---
status: ACTIVE
owner: research
applies_to_commit: 8692afefe1f6cc82ab1f276fca788888f9f30f3e
last_verified: 2026-08-28
---

# Research Lifecycle

```text
immutable inputs
    -> frozen feature/label/split/model/portfolio contracts
    -> per-fold fitted-state isolation
    -> ordered non-overlapping OOS predictions
    -> continuous research portfolio account
    -> Research Gate
    -> research artifact graph
    -> at most RESEARCH_PROMOTED
```

Every research run binds DataRelease, DatasetVersion, FeatureSnapshot, AlphaPack, LabelSpec, SplitSpec,
model profile, strategy/portfolio policy and source revision. Changing any governed input creates a new
run identity.

## Isolation

- fit processors and models per fold; never share fitted state across folds;
- enforce purge, embargo and label-lookahead gaps;
- stitch only ordered, non-overlapping OOS predictions;
- keep final holdout sealed from feature/model/hyperparameter/portfolio selection;
- preserve one continuous portfolio account across rolling fold boundaries;
- fail closed on cache, checkpoint, snapshot or lineage drift.

## Gate logic

The production stability branch is:

```text
(ICIR >= 0.50 OR RankICIR >= 0.50)
AND ExcessIR >= 0.50
AND all other research, portfolio and lineage gates
```

`RESEARCH_REVIEW` requires `ICIR >= 0.30 OR RankICIR >= 0.40` plus all other hard conditions.
It preserves evidence but does not publish an execution candidate.

## Active program

The stability-diagnostics program performs only temporal-stability and causal-regime diagnostics over
frozen rejected candidate-program anchors. It creates no formal candidate, performs no model selection,
opens no final holdout and authorizes no publishing. See
[Alpha Research Stability Diagnostics](alpha_research_stability.md).
