---
status: ACTIVE
owner: architecture
applies_to_commit: 8037585f727dd0d1358b5c486ab0867655cd5d90
last_verified: 2026-09-05
---

# P4 — Qlib Native Compatibility & Superset Contract

P4 establishes the architectural contract that `qlib-platform = Qlib native capability + additive institutional research controls`.

| Workstream | Contract |
| --- | --- |
| P4.0 Capability Manifest | Pin the upstream Qlib version and machine-check core/optional capability imports. |
| P4.1 Generic Object / Model | Delegate arbitrary importable classes to Qlib `init_instance_by_config`; no platform allowlist in the native lane. |
| P4.2 qrun / task_train / Recorder | Delegate execution to upstream `qlib.cli.run.workflow` and `qlib.model.trainer.task_train`. |
| P4.3 Dataset / Handler / Processor | Provide generic typed convenience factories without restricting upstream configuration. |
| P4.4 Strategy / Executor / Backtest | Preserve arbitrary native Qlib strategy/executor configs while leaving certified platform policies strict. |
| P4.5 Recorder federation | Index upstream Recorder metadata/references in ExperimentStore without claiming artifact immutability. |
| P4.6 Regression CI | Fail on lost required Qlib capabilities and explicitly test optional model/RL stacks. |
| P4.7 Packaging | Make pinned pyqlib part of the platform substrate and expose heavy capability extras. |

## Superset invariants

1. Importing `qlib-platform` compatibility code does not monkey-patch Qlib.
2. The native lane does not rewrite upstream workflow configuration.
3. A new/custom importable Qlib Model, Dataset, Handler, Processor, Strategy, or Executor does not require a platform registry entry.
4. Curated platform registries continue to represent certified behavior only.
5. Qlib Recorder/Experiment state remains owned by Qlib; federation is an index, not a replacement.
6. Capability loss against the pinned upstream version is a CI failure.
7. Qlib version upgrades require an explicit capability-manifest update and review.

## Governance boundary

This contract is infrastructure-only. The active Phase 3-D diagnosis-only restrictions remain unchanged: no candidate creation, model selection, final-holdout access, or publishing is authorized by P4.
