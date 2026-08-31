---
status: ACTIVE
owner: architecture
applies_to_commit: 8692afefe1f6cc82ab1f276fca788888f9f30f3e
last_verified: 2026-08-28
---

# Identity and Lineage

## Local governed research chain

```text
DataRelease
    -> DatasetVersion
    -> FeatureSnapshot
    -> PredictionSnapshot
    -> RealizedLabelSnapshot
    -> PredictionEvaluationSnapshot
    -> research manifest / MODEL_RELEASE
    -> MODEL_TOPK
    -> PortfolioPolicy
    -> TARGET_PORTFOLIO
```

These identities are related but not interchangeable.

| Identity | Meaning | Verification boundary |
| --- | --- | --- |
| DataRelease | immutable upstream fact release | manifest identity, component identity, roles/schema, file SHA/size |
| DatasetVersion | immutable Qlib materialization | version identity, partition checksums, semantic parent binding |
| FeatureSnapshot | fitted feature recipe plus immutable partitions | governed verifier checks recipe, coverage, files and upstream bindings |
| PredictionSnapshot | score/label payload under a complete research contract | snapshot identity, payload SHA/schema/rows/coverage |
| RealizedLabelSnapshot | matured outcomes from one DataRelease and LabelSpec | maturity/calendar binding, snapshot identity, payload SHA/schema/rows/coverage |
| PredictionEvaluationSnapshot | monitoring evidence joining one prediction and realized-label snapshot | exact parent IDs, DataRelease/LabelSpec equality, payload SHA/schema/rows |
| ModelRelease | governed fitted model artifact | model/runtime/research manifest lineage |
| TARGET_PORTFOLIO | research target weights after PortfolioPolicy | payload checksum, policy identity, parent artifact graph, DataRelease binding |

### DataRelease

`dataReleaseId` is `ds_<identitySha256>`. The identity excludes publication-only fields; the manifest
has a separate canonical `manifestSha256`. Verification checks all declared component and file
identities and fails closed on drift.

### DatasetVersion

DatasetVersion identity is derived from dataset name, layer, sorted partition path/size/SHA, semantic
contract and sorted parents. Its semantic contract carries the DataRelease binding. `dataset-verify`
verifies this object; it does not verify the DataRelease manifest itself.

### FeatureSnapshot

`featureRecipeId` hashes the recipe contract. `featureSnapshotId` hashes recipe ID, coverage and files.
Full checksum and identity revalidation is guaranteed by governed paths such as Phase 3 contract
verification; callers of the general loader must request checksum verification when required.

### PredictionSnapshot

`snapshotId` binds the complete prediction contract and payload metadata. The contract includes
DataRelease, AlphaPack, FeatureSnapshot, LabelSpec, SplitSpec, model/profile, fold and feature-set
identities. Loading rechecks payload SHA, schema, rows and coverage.

### Production feedback snapshots

`RealizedLabelSnapshot` accepts labels only when every signal date is mature under the supplied pinned
trading calendar and observation cut. Its identity binds that calendar hash, DataRelease, LabelSpec,
source artifact and payload. `PredictionEvaluationSnapshot` requires complete key coverage and equal
DataRelease/LabelSpec bindings before computing daily IC, RankIC, top-bottom spread and rolling RankIC.
These artifacts are monitoring evidence only: they cannot access the sealed final holdout or trigger
candidate selection, deployment or publication.

## Artifact Contract v2 handoff graph

The exported v2 graph is narrower than the local research chain:

```text
MODEL_RELEASE
    -> STRATEGY_POLICY
    -> SIGNAL_SNAPSHOT
    -> TARGET_PORTFOLIO
    -> VALIDATION_RESULT
```

Parent edges are explicit, and all nodes carry one `dataReleaseId`. DatasetVersion, FeatureSnapshot and
PredictionSnapshot are not separate v2 graph nodes today; their local lineage is carried through the
source research manifest and its checksum.

`TARGET_PORTFOLIO` is the only cross-repository artifact with execution semantics. The other nodes form
the research lineage and validation envelope. The bundle does not transfer orders, fills, positions
ledger, broker state or authoritative LEAN semantics.

## Fail-closed wording

Do not claim that the entire artifact envelope is content-addressed by every record field. Current
artifact identity binds the payload plus selected identity fields. Governed consumers must still verify
schema, parents, DataRelease equality, payload checksums and source-manifest lineage separately.
