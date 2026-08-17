# Alpha Research Phase 3 — Stability and Regime Diagnosis

Phase 3 changes the research question from factor expansion to temporal alpha stability. Its immutable
entry condition is a completed Phase 2 acceptance artifact containing exactly
`H001–H005/H101–H106`, with `acceptedCount=0`, every candidate still marked `REJECTED`, and both
holdout selection and publishing disabled. The acceptance must be hash-bound to the candidate-metrics
collector and the exact Phase 2 evidence index that produced it.

This first implementation is Phase 3-D only. It covers P3-D00 through P3-D04 and cannot register a
formal hypothesis, produce a Research Candidate, select a model, run P2-R01 through P2-R03, or open the
final holdout.

## Frozen diagnostic program

`configs/research/ashare_phase3_v1.yaml` freezes three existing rolling-OOS anchors:

| Anchor | Role | Phase 2 experiment |
| --- | --- | --- |
| `P2-06_A4_RIDGE` | linear control | P2-06 / A4 Ridge |
| `P2-07_A4_XGB` | nonlinear anchor | P2-07 / A4 XGBoost |
| `P2-08_A5_XGB` | enriched anchor | P2-08 / A5 XGBoost |

The existing `configs/regimes/ashare_regime_v1.yaml` remains unchanged. Expanding volatility,
activity, and breadth thresholds continue to use history shifted by one session. Phase 3 generalizes
the model diagnostic API to accept explicit anchor names and explicit candidate/baseline descriptive
comparisons; the Phase 1 ridge/LightGBM/XGBoost behavior remains the default.

The state boundary for this PR is:

```text
PHASE2_COMPLETE_REJECTED
        -> PHASE3_DESIGN_LOCKED
        -> PHASE3_DIAGNOSIS_COMPLETE
        -> confirmation NOT_STARTED
```

At every state, `publishingAuthorized=false`, formal candidate count is zero, and the inherited final
holdout remains sealed.

## P3-D00 through P3-D04

- P3-D00 freezes the Phase 2 acceptance, candidate-metrics collector, evidence index, and formal
  eight-check DataRelease-v2 acceptance SHA-256 values, contract-lock identity, DataRelease,
  DatasetVersion, FeatureSnapshot, labels, anchor PredictionSnapshots, regime spec, source commit,
  and diagnostic implementation hashes.
- P3-D01 derives daily IC, RankIC, TopK forward-label spread, TopK turnover, rolling 63/126/252-session
  stability, and contiguous negative rolling-RankIC failure episodes.
- P3-D02 applies the existing causal regime engine and derives model-by-dimension-by-state diagnostics.
- P3-D03 compares pre/post 20- and 63-session behavior around observed regime transitions. These tables
  are descriptive and are never promoted to confirmatory tests automatically.
- P3-D04 groups rolling-OOS performance by sessions since each fold test began. Calendar days from the
  recorded train end are retained as an audit field. This is model-vintage decay evidence, not a new
  training-window experiment.

`topk_spread` uses the embedded five-day forward labels and is a diagnostic signal spread, not a
realized portfolio P&L series. Phase 3-D prohibits external portfolio manifests because they are not
part of the frozen PredictionSnapshot/DataRelease evidence chain. Portfolio excess return is therefore
explicitly `INPUT_UNAVAILABLE`; it is never synthesized from labels.

## Commands

Run all commands from the repository root with the repository-local interpreter:

```bash
RepoPython=.venv/bin/python

$RepoPython -m tushare_qlib \
  --config configs/pipeline.yaml \
  phase3-validate \
  --phase2-acceptance /path/to/phase2_acceptance.json \
  --phase2-evidence /path/to/phase2_evidence_index_v1.json \
  --phase2-data-acceptance /path/to/phase2_data_release_acceptance.json \
  --contract configs/research/ashare_phase3_v1.yaml \
  --output /path/to/phase3_design_lock.json

$RepoPython -m tushare_qlib \
  --config configs/pipeline.yaml \
  phase3-plan \
  --contract-lock /path/to/phase3_design_lock.json \
  --output /path/to/phase3_plan.json

$RepoPython -m tushare_qlib \
  --config configs/pipeline_phase2.yaml \
  phase3-diagnose \
  --contract-lock /path/to/phase3_design_lock.json \
  --plan /path/to/phase3_plan.json \
  --evidence /path/to/phase2_evidence_index_v1.json \
  --regimes configs/regimes/ashare_regime_v1.yaml \
  --output /path/to/phase3_evidence/diagnosis_v1
```

## Cross-machine read-only verification

After a completed D00–D04 bundle exists, create its portable evidence package outside the source checkout:

```bash
$RepoPython -m tushare_qlib \
  --config configs/pipeline.yaml \
  phase3-portable-export \
  --contract-lock /path/to/phase3_design_lock.json \
  --plan /path/to/phase3_plan.json \
  --diagnosis /path/to/phase3_evidence/diagnosis_v1 \
  --contract configs/research/ashare_phase3_v1.yaml \
  --data-root /path/to/quant-data-root \
  --output /portable-storage/phase3_evidence_v1
```

Move the resulting directory without changing its contents. On a clean checkout at the source commit, run:

```bash
$RepoPython -m tushare_qlib \
  --config configs/pipeline.yaml \
  phase3-portable-verify \
  --package /relocated/phase3_evidence_v1
```

The verifier performs no retraining and does not run D00–D04. It only recomputes the package inventory,
Phase 2/DataRelease/FeatureSnapshot/PredictionSnapshot bindings, implementation hashes, diagnosis artifact
checksums and isolation state. It rejects symlinks, path escapes, missing or extra files, a different source
commit, any final-holdout access, candidate creation or publishing authorization.
`phase3-validate` records the current source revision; `phase3-diagnose` requires that revision to be
clean, committed, and identical to the lock. It also requires the immutable plan, checks its
`planSha256` and design-lock binding, then rechecks every locked input and implementation hash before
reading predictions. The diagnosis writes an immutable directory; rerunning against an existing
directory succeeds only when its evidence-index checksum, exact artifact set, row counts, state and
holdout/publishing fields, plan binding, and every artifact checksum still match.

## Diagnostic evidence bundle

The bundle contains:

```text
anchor_predictions_index.json
daily_model_metrics.parquet
rolling_63_rank_ic.parquet
rolling_126_rank_ic.parquet
rolling_252_rank_ic.parquet
failure_windows.parquet
regime_labels.parquet
regime_model_metrics.parquet
regime_transition_metrics.parquet
training_age_decay.parquet
phase3_diagnostics_report.json
phase3_diagnostics_report.md
phase3_evidence_index.json
```

The evidence index records the plan SHA-256, artifact SHA-256 values and lineage, ends in
`PHASE3_DIAGNOSIS_COMPLETE`, and explicitly records `confirmationState=NOT_STARTED`, an empty formal
candidate list, and no final-holdout access.

Phase 3-C is intentionally outside this PR. Diagnostic findings must be reviewed, converted into a
small new hypothesis family, and frozen in a separate confirmation lock before matched tests and
dependence-aware multiple testing can begin.
