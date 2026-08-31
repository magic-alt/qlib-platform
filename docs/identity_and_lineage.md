---
status: ACTIVE
owner: architecture
applies_to_commit: 4f3f4369b6e55186967bc726bb8dd87fff0e5d70
last_verified: 2026-08-31
---

# Identity and Lineage

## Governed research chain

The promotion path and the production-feedback path are related but deliberately not one linear lifecycle.

```text
DataRelease
    -> DatasetVersion
    -> FeatureSnapshot
    -> PredictionSnapshot
    -> research manifest / MODEL_RELEASE
    -> MODEL_TOPK
    -> PortfolioPolicy
    -> TARGET_PORTFOLIO

DataRelease-bound matured outcomes
    -> RealizedLabelSnapshot -----+
                                  |
PredictionSnapshot ---------------+-> PredictionEvaluationSnapshot
                                      (monitoring only)
```

`PredictionEvaluationSnapshot` is a side-car monitoring artifact. It is not a prerequisite for `MODEL_RELEASE`, does not sit on the promotion chain, and cannot trigger candidate selection, deployment or publication.

These identities are related but not interchangeable.

| Identity | Meaning | Verification boundary |
| --- | --- | --- |
| DataRelease | immutable upstream fact release | manifest identity, component identity, roles/schema, file SHA/size |
| DatasetVersion | immutable Qlib materialization | version identity, partition checksums, semantic parent binding |
| FeatureSnapshot | fitted feature recipe plus immutable partitions | governed verifier checks recipe, coverage, files and upstream bindings |
| PredictionSnapshot | score/label payload under a complete research contract | snapshot identity, payload SHA/schema/rows/coverage |
| RealizedLabelSnapshot | matured outcomes from one DataRelease and LabelSpec | maturity/calendar binding, snapshot identity, payload SHA/schema/rows/coverage |
| PredictionEvaluationSnapshot | monitoring evidence joining one prediction and realized-label snapshot | exact parent IDs, DataRelease/LabelSpec equality, payload SHA/schema/rows |
| ModelRelease | governed fitted model artifact | model/runtime/research-manifest lineage |
| TARGET_PORTFOLIO | research target weights after PortfolioPolicy | payload checksum, policy identity, parent artifact graph, DataRelease binding |

## DataRelease

`dataReleaseId` is `ds_<identitySha256>`. The identity excludes publication-only fields; the manifest has a separate canonical `manifestSha256`. Verification checks declared component and file identities and fails closed on drift.

Use:

```text
release verify <DATA_RELEASE_REF>
```

A DataRelease ID is not accepted as a substitute for `--dataset-ref`.

## DatasetVersion

DatasetVersion identity is derived from dataset name, layer, sorted partition path/size/SHA, semantic contract and sorted parents. Its semantic contract carries the DataRelease binding. `dataset-verify` verifies the DatasetVersion; it does not replace independent DataRelease verification.

Research and inference should pin the resolved immutable version, even when an alias such as `research-current` is used at process entry.

## Verification levels

Both release and dataset verification expose explicit integrity levels:

- `manifest` — validate manifest/schema/identity/inventory without reading every payload byte;
- `sampled` — validate a deterministic bounded sample plus coverage sentinels;
- `deep` — verify all declared payload files and emit verification evidence.

Use `deep` for governed research resolution, promotion, migration and certification. `--reuse-receipt` reuses valid evidence metadata but does not turn current payload validation into a trust-on-first-use shortcut.

## FeatureSnapshot

`featureRecipeId` hashes the recipe contract. `featureSnapshotId` hashes recipe ID, coverage and files. Governed verification checks recipe, file identity and upstream binding; callers of lower-level loaders must explicitly request the checksum strength their workflow requires.

## PredictionSnapshot

`snapshotId` binds the complete prediction contract and payload metadata. The contract includes DataRelease, AlphaPack, FeatureSnapshot, LabelSpec, SplitSpec, model/profile, fold and feature-set identities. Loading rechecks payload SHA, schema, rows and coverage.

## Production feedback snapshots

`RealizedLabelSnapshot` accepts labels only when every signal date is mature under the supplied pinned trading calendar and observation cut. Its identity binds the calendar hash, DataRelease, LabelSpec, source artifact and payload.

`PredictionEvaluationSnapshot` requires complete key coverage and equal DataRelease/LabelSpec bindings before computing daily IC, RankIC, top-bottom spread and rolling RankIC. These artifacts are monitoring evidence only: they cannot access the sealed final holdout or authorize candidate selection, deployment or publication.

See [Production Feedback](production_feedback.md).

## Artifact Contract v2 handoff graph

The exported v2 graph is narrower than the local research lineage:

```text
MODEL_RELEASE
    -> STRATEGY_POLICY
    -> SIGNAL_SNAPSHOT
    -> TARGET_PORTFOLIO
    -> VALIDATION_RESULT
```

Parent edges are explicit, and all nodes carry one `dataReleaseId`. DatasetVersion, FeatureSnapshot and PredictionSnapshot are not separate v2 graph nodes today; their local lineage is carried through the source research manifest and its checksum.

`TARGET_PORTFOLIO` is the only cross-repository artifact with execution semantics. The other nodes form the research lineage and validation envelope. The bundle does not transfer orders, fills, positions, ledger, broker state or authoritative LEAN semantics.

## Fail-closed wording

Do not claim that the entire artifact envelope is content-addressed by every record field. Current artifact identity binds the payload plus selected identity fields. Governed consumers must still verify schema, parents, DataRelease equality, payload checksums and source-manifest lineage separately.
