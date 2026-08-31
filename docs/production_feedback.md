---
status: ACTIVE
owner: operations
applies_to_commit: 4f3f4369b6e55186967bc726bb8dd87fff0e5d70
last_verified: 2026-08-31
---

# Production Feedback

Production feedback is an immutable monitoring path back into the Research Plane. It is deliberately separate from broker/OMS/ledger ownership and from the model-promotion path.

## Purpose

The implemented foundation answers: after a governed prediction has matured, did its realized cross-sectional behavior remain consistent with the research evidence?

```text
verified PredictionSnapshot ------------------+
                                                |
DataRelease-bound matured outcomes              |
    -> RealizedLabelSnapshot ------------------+-> PredictionEvaluationSnapshot
                                                    -> monitoring evidence
```

Neither feedback artifact authorizes candidate selection, final-holdout access, model promotion, deployment or publication.

## RealizedLabelSnapshot

Create a matured-label snapshot with:

```bash
$RepoPython -m tushare_qlib feedback-build-labels \
  --labels /path/to/labels.parquet \
  --calendar /path/to/pinned_calendar.txt \
  --observed-through YYYY-MM-DD \
  --data-release-id ds_<sha256> \
  --label-spec-id <LABEL_SPEC_ID> \
  --horizon-days 5 \
  --signal-lag-days 1 \
  --price-field close \
  --source-artifact-id <SOURCE_ARTIFACT_ID> \
  --output /path/to/realized_labels.parquet
```

The writer rejects labels that are not mature under the pinned trading calendar/observation cut. The snapshot identity binds the DataRelease, LabelSpec, calendar, source artifact and payload.

The input Parquet may carry `datetime`/`instrument` as columns; the CLI normalizes them to the governed index before writing.

## PredictionEvaluationSnapshot

Evaluate one verified PredictionSnapshot against one compatible RealizedLabelSnapshot:

```bash
$RepoPython -m tushare_qlib feedback-evaluate \
  --predictions /path/to/prediction_snapshot \
  --realized-labels /path/to/realized_labels.parquet \
  --output /path/to/prediction_evaluation.parquet \
  --topk 50 \
  --min-cross-section 20 \
  --rolling-window 20
```

The evaluator verifies both parents and requires compatible DataRelease/LabelSpec bindings and complete prediction/label key coverage. Daily evidence includes:

- sample count;
- IC;
- RankIC;
- top-bottom label spread;
- cross-section quality status;
- rolling RankIC summary/evidence.

If the generated evaluation decision is not `PASS`, the CLI exits with status 2 after writing the evidence. A non-PASS evaluation is monitoring evidence, not permission to refit/reselect automatically.

## What this path does not ingest

Do not feed mutable/raw execution state into this repository as authoritative truth. The Research Plane must not own:

- live orders or order updates;
- raw broker fills as mutable ledger state;
- current broker positions/account balances;
- OMS state;
- hard-risk decisions;
- execution ledger reconciliation.

A future platform-owned aggregate execution-feedback contract may provide immutable, explicitly bound evaluation facts. That boundary must remain distinct from execution ownership.

## Operational guidance

- pin and verify the parent PredictionSnapshot and DataRelease before generating feedback evidence;
- store outputs in new immutable paths; do not overwrite an earlier evaluation;
- preserve the observation cut and calendar identity so later data cannot leak backward;
- compare feedback over time as monitoring evidence, not as an implicit training trigger;
- any retraining trigger should create reviewable evidence and remain separate from approval/promotion.

See [Identity and Lineage](identity_and_lineage.md), [Production ML Phase 4](production_ml_phase4.md), and [Architecture Boundary](architecture_boundary.md).
