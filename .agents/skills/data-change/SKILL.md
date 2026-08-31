---
name: data-change
description: Implement or review qlib-platform ingestion, DataRelease, DatasetVersion, PIT, daily-sync, verification, registry, migration, or Qlib materialization changes while preserving immutable data lineage.
---

# Governed data change

Use this skill for changes to local/TuShare ingestion, Platform release consumption, Bronze/Silver/Gold data, PIT normalization, DataRelease publication, DatasetVersion materialization, verification, aliases, migration, registry, or daily synchronization.

Before changing behavior, identify the source mode, release profile/capabilities, mutable working views, immutable outputs, alias transitions, and the exact consumer that depends on the data.

Preserve these invariants:

- `DataRelease` and `DatasetVersion` are distinct identities and must be verified at their own boundaries;
- local/TuShare research publication and Platform-produced release consumption are both supported modes; do not silently merge their ownership or capability semantics;
- published releases, dataset versions, manifests, partitions, parents, and checksums are immutable;
- `current` working views and aliases may move only through atomic, validated publication; a failed build/sync must leave the previous governed alias usable;
- PIT visibility, trusted publication timestamps, trading calendar, corporate-action/adjustment semantics, security state, units, and missing-value policy must remain causal and explicit;
- verification levels (`manifest`, `sampled`, `deep`) must not be weakened or relabeled; governed promotion/certification paths remain fail closed;
- migration and registry rebuild must preserve source bytes, identity, recoverability, and audit evidence; never repair immutable data in place;
- release capabilities must block exploratory imports from Phase 2/3, promotion, TARGET_PORTFOLIO, or Artifact v2 paths when the profile does not authorize them.

Treat `backfill`, `sync-*`, `daily-sync`, `curate*`, `stage-*`, `dump-*`, `dataset-build`, `dataset-promote`, `release build-*`, `release import-qlib`, `release promote`, migration apply, and registry rebuild as state-changing. Obtain explicit authorization for real data writes and report affected roots/aliases.

Tests should include deterministic identity/checksum assertions and negative cases for PIT leakage, missing components, tampering, alias safety, capability rejection, partial publication, and migration/resume behavior as relevant.
