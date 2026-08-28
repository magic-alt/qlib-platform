---
status: ACTIVE
owner: operations
applies_to_commit: 8692afefe1f6cc82ab1f276fca788888f9f30f3e
last_verified: 2026-08-28
---

# Model Lifecycle

## Research model

`train-select` and `research-run` create research evidence under a frozen model profile. Switching
Ridge, LightGBM, XGBoost or PyTorch must not silently change DataRelease, FeatureSnapshot, label, split or
portfolio contracts.

## Local ModelRelease

`model-refit` creates a new immutable local release from an approved recipe and explicit DatasetVersion.
It must record runtime identity, training window, upstream lineage and parity evidence.

## Local deployment selection

`model-status` reads the current local selection. `model-deploy` and `model-rollback` change it and
therefore require explicit authorization. A rollback selects a previously verified local ModelRelease for
future inference; it does not mutate the artifact or change `platform` deployment state.

## Inference

`live-inference --dataset-ref` accepts a DatasetVersion ID/alias. It must reject DataRelease/DatasetVersion,
feature, preprocessing, model or date drift. `daily-signal-run` writes artifacts/outbox and is not a
read-only health check.

Production model approval, authoritative execution validation and Production rollback belong to
`magic-alt/platform`.
