# Full Walk-forward Acceptance

`FULL_WALK_FORWARD_V1` certifies temporal causality, per-fold fitted-state isolation,
continuous OOS portfolio state, checkpoint recovery, deterministic replay, and final-holdout
isolation. Research quality is reported independently and may be `REJECT` while system and
walk-forward acceptance pass.

## Preconditions

- Run from a clean task commit using `.venv/bin/python`.
- Pin one immutable DataRelease through `DATASET_RELEASE_ID` and `QUANT_DATA_ROOT`.
- Materialize the governed FeatureSnapshot before acceptance. Every acceptance run requires
  `cacheStatus=REUSED` and `rawMaterializationCalls=0`.
- Use the same date window, config, label, portfolio policy, and gate thresholds for all models.

The runner validates fold boundaries against the governed trading calendar. Purge and embargo
must each cover the complete label lookahead. The maximum train-label information date must be
strictly earlier than the first validation decision date.

## Model evidence runs

Run one uninterrupted baseline and one interrupted/resumed sequence for each profile. Separate
checkpoint namespaces keep the baseline independent from the recovery sequence.

```bash
.venv/bin/python -m tushare_qlib --config configs/pipeline.yaml research-run \
  --mode walk-forward --stage release --full-acceptance \
  --checkpoint-namespace ridge-baseline \
  --model-profile configs/model_profiles/ridge_golden_v1.yaml

.venv/bin/python -m tushare_qlib --config configs/pipeline.yaml research-run \
  --mode walk-forward --stage release --full-acceptance \
  --checkpoint-namespace ridge-resume --interrupt-after-fold 3 \
  --model-profile configs/model_profiles/ridge_golden_v1.yaml

.venv/bin/python -m tushare_qlib --config configs/pipeline.yaml research-run \
  --mode walk-forward --stage release --full-acceptance \
  --checkpoint-namespace ridge-resume \
  --model-profile configs/model_profiles/ridge_golden_v1.yaml
```

Repeat with `lightgbm_cpu_m5.yaml` and `xgboost_cpu_v1.yaml`, using distinct namespaces. The
interrupted command is expected to exit non-zero after atomically publishing folds 1–3 and before
fold 4. On resume, validated folds are `REUSED` and fold 4 is trained normally. A separate
mid-fold process-kill negative test is covered by the rule that no checkpoint is reusable until
its manifest and every artifact checksum have been atomically committed.

Each completed run writes a self-contained `walk_forward_evidence.json` beside aggregate
prediction, label, portfolio, and holdings artifacts. Rolling fold portfolios are never executed;
all rolling predictions drive one predictions-only backtest and one continuous account.

## Corruption injection

After preserving the resumed evidence path, alter one test-only fold payload referenced by the
namespace checkpoint, such as `rolling_03`'s `oos_predictions.parquet`, then rerun the same
namespace. The checkpoint must become `INVALIDATED_REBUILT`; all other valid folds remain
`REUSED`. Never perform this injection against a production or irreplaceable artifact root.

Feature-partition corruption, OOS duplicate keys, missing or overlapping test dates, out-of-order
rows, insufficient label gaps, and cash/holding state resets are also blocking failures.

## Final certification

Pass the baseline and resumed run directories for all three models, plus the corruption-rebuild
directory, to the final comparator:

```bash
.venv/bin/python scripts/compare_full_walk_forward_runs.py \
  --ridge <RIDGE_BASELINE> <RIDGE_RESUMED> \
  --lightgbm <LGB_BASELINE> <LGB_RESUMED> \
  --xgboost <XGB_BASELINE> <XGB_RESUMED> \
  --corruption-rebuild <CORRUPTION_REBUILD> \
  --output <EVIDENCE_DIR>/full_walk_forward_acceptance.json
```

The comparator fails closed unless DataRelease, FeatureSnapshot, AlphaPack, label, fold calendar,
portfolio policy, thresholds, and code commit match across models; labels match exactly;
model predictions differ; and each model's aggregate prediction, portfolio, labels, and holdings
are byte-exact between uninterrupted and resumed runs. It also requires a proven corrupted-fold
invalidation/rebuild. Only then does it write:

```text
systemAcceptance: PASS
walkForwardAcceptance: PASS
performanceAcceptance: BASELINE_RECORDED
```

The final holdout is accessed only after `research_selection_lock.json` fixes the research
selection contract. Holdout execution uses a non-publishing mode, so it cannot create selections,
signal targets, or a TargetPortfolio before the complete system contract passes.
