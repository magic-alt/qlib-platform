---
status: HISTORICAL
owner: research
applies_to_commit: 8692afefe1f6cc82ab1f276fca788888f9f30f3e
last_verified: 2026-08-28
superseded_by: alpha_research_phase_3.md
---

# Alpha Research Phase 2

> Status: COMPLETED / FROZEN. This is the predecessor protocol for the active Phase 3-D program.
> Do not treat its commands as the current research plan.

Phase 2 asks which economically distinct signals add stable, implementable
rolling-OOS information beyond the registered China A-share benchmark factors.
It does not authorize a production model or an investment recommendation.

## Immutable entry contract

Every program starts from the real local Phase 1 synthesis manifest. Its
`primaryRecommendation` is the only workstream switch:

```bash
.venv/bin/python -m qlib_platform --config configs/pipeline.yaml candidate-validate \
  --synthesis-manifest /path/to/alpha_phase_1_manifest.json \
  --output /path/to/phase2_contract_lock.json

.venv/bin/python -m qlib_platform --config configs/pipeline.yaml candidate-plan \
  --contract-lock /path/to/phase2_contract_lock.json \
  --output /path/to/phase2_experiment_plan.json
```

The lock freezes the DataRelease profile, PIT universe, label, rolling split,
cost model, hypotheses, directions, definitions, normalizations, multiple-test
protocol, robustness gates, and final-holdout policy. Plans set
`publishingAuthorized=false` and exclude final-holdout observations.

| Phase 1 recommendation | Phase 2 route |
| --- | --- |
| `ALPHA_PACK_V2` | Data v2 → benchmarks → candidates → incremental acceptance |
| `REGIME_AWARE_RESEARCH` | Alpha route, then causal overlay studies |
| `PORTFOLIO_CONSTRUCTION` | Rank buffer first, then the alpha route |
| `XGBOOST_TUNING` | Eight fixed trials, only with `RECOVERABLE` evidence |
| `NO_GO_NEW_ALPHA` | Empty experiment plan and rejection report only |

## DataRelease v2 boundary

`platform` publishes `ashare_qlib_research_v2`. The profile requires PIT
industry, fundamentals schema `2`, and `qlib-staging-v2`. `qlib-platform`
verifies the immutable manifest, component identities, file hashes and sizes,
required roles, and exact component schemas before materialization. DataRelease
v1 and its FeatureSnapshot remain unchanged.

The v2 PIT service applies a report or revision on the first open session after
the later of `ann_date` and `f_ann_date`. TTM and prior-year comparable facts
belong to upstream typed-source normalization; factor ratios belong here.
Industrial profitability, investment, accrual, and fundamental-momentum ratios
are missing—not zero—for registered non-applicable financial industries.

Publishing a real release or running a backfill remains a separately authorized
state-changing operation.

The v2 acceptance is intentionally limited to PIT leakage, PIT industry,
expanded fundamentals, dataset build, FeatureSnapshot checksum, label timing,
deterministic Ridge, and a mini Qlib→LEAN E2E. After producing those eight
evidence hashes, seal them with `candidate-data-accept`; this does not repeat the
old corruption and three-model certification suites.

## Feature sets and experiment matrix

`TushareAsharePhase2` loads a fixed superset. `Phase2FeatureSetProcessor` then
constructs cross-sectional features and drops every field outside the selected
set before fitting. The selected set and checksum are in the research contract.

The A0–A7 ladder is fixed. `VP1` is Value + Profitability for P2-05; `LVR1` is
LowRisk residualized daily against Value, Profitability, Size, and industry;
`I1` contains only the six registered economic interactions. A7 refuses to run
without a frozen non-empty technical representative list.

P2-01 through P2-10 are defined in `EXPERIMENT_MATRIX`. Ridge is the primary
incremental test, XGBoost tests bounded nonlinearity, and LightGBM is a sanity
comparator rather than multiplying every hypothesis.

Use the v2 config and the feature set/model named by the immutable plan, for
example:

```bash
.venv/bin/python -m qlib_platform --config configs/pipeline_candidate_research.yaml research-run \
  --mode walk-forward --stage release --feature-set A1 \
  --model-profile configs/model_profiles/ridge_golden_v1.yaml
```

A7 additionally requires one `--selected-technical NAME` argument per frozen
cluster representative. The processor refuses an unsealed empty A7.

The A0–A7 runs are mechanism ablations, not formal hypothesis evidence. Each
H001–H106 primary test is a separate, frozen nested Ridge pair. Bind every run
to the contract definition instead of manually selecting a broad feature set:

```bash
.venv/bin/python -m qlib_platform --config configs/pipeline_candidate_research.yaml research-run \
  --mode fixed --stage release --hypothesis-id H104 --hypothesis-role candidate \
  --contract-lock /path/to/phase2_contract_lock.json \
  --model-profile configs/model_profiles/ridge_golden_v1.yaml \
  --dataset-ref DATASET_VERSION_ID
```

Repeat the same frozen fold with `--hypothesis-role baseline`. Formal
hypothesis runs reject XGBoost, manual feature-set overrides, and generic
`--mode walk-forward`, because that command includes the sealed final holdout.
H001–H005 each add only their registered characteristic to fixed controls;
H101–H106 each add only one registered interaction to its two main effects.

## Acceptance

The primary family reports HAC t-statistics, BH q-values, empirical-Bayes local
FDR, and circular-block Romano–Wolf stepdown p-values. A candidate is rejected
with explicit reason codes unless it passes coverage, oriented RankIC, positive
folds, worst fold and worst 252-session window, leave-one-year-out robustness,
positive nested-Ridge increment, turnover, and 1.5× cost gates.

Do not assemble `candidate_metrics.json` by hand. After all registered runs are
complete, create a `phase2_evidence_index_v1` that contains paths only: the
DataRelease manifest, DatasetVersion, FeatureSnapshot, canonical rolling-OOS
labels, benchmark factor panel, P2-01 through P2-10 run manifests, and exactly
one primary evidence bundle for each of H001 through H106. Each hypothesis
bundle names its immutable run manifests, candidate and baseline
PredictionSnapshots, baseline feature set/model and run manifests, and
predictions-only candidate and baseline portfolio manifests. Paths may be
absolute or relative to the evidence index.

```bash
.venv/bin/python -m qlib_platform --config configs/pipeline.yaml candidate-collect \
  --contract-lock /path/to/phase2_contract_lock.json \
  --evidence /path/to/phase2_evidence_index.json \
  --output /path/to/candidate_metrics.json
```

The collector verifies the DataRelease profile and component schemas,
DataReleaseId, DatasetVersion, FeatureSnapshot partitions, LabelSpec, fold
calendar, research experiment and feature-set checksums, model profile, and
PredictionSnapshot/portfolio reuse. It rejects promotion-authorized artifacts,
regime rules, any final-holdout evidence, a partial or duplicated hypothesis
family, P2 ablation drift, hypothesis-definition drift, broad ablations used as
formal tests, and candidate PredictionSnapshot reuse across hypotheses.
Candidate IDs must exactly equal the frozen hypothesis IDs
`H001–H005/H101–H106`; aliases and suffixes are rejected.

All 11 paired candidate-minus-baseline daily RankIC series enter one
date-by-hypothesis matrix before HAC, BH-FDR, local FDR, or Romano–Wolf is
computed. Candidate and baseline dates must match exactly. Robustness metrics
are also derived by the collector: coverage uses canonical eligible label keys;
fold and 252-session minima use oriented daily RankIC; leave-one-year retention
is the minimum leave-one-year-out mean divided by the full-sample mean; turnover
increase is the candidate mean daily turnover minus the baseline; stressed net
spread is mean candidate return minus benchmark and the registered cost
multiple. The resulting artifact is immutable and remains research-only.

```bash
.venv/bin/python -m qlib_platform --config configs/pipeline.yaml candidate-accept \
  --contract-lock /path/to/phase2_contract_lock.json \
  --candidate-metrics /path/to/candidate_metrics.json \
  --output /path/to/candidate_acceptance.json
```

`candidate-accept` consumes the collector object itself, verifies its
`collectorSha256`, contract lock and evidence-index SHA-256, and carries those bindings into the
acceptance artifact. A raw candidate list is not a valid CLI input.

Accepted objects are still `RESEARCH_CANDIDATE`, not selected securities or
production strategies.

## Regime, portfolio, and final holdout

The regime overlay uses only T-1 data and reuses one PredictionSnapshot. It
tests LowRisk weights of 0.5 and 0 in high volatility, plus gross exposure
`clip(lagged expanding-median volatility / lagged volatility, 0.5, 1.0)`.
Regime-specific ML models and leverage are prohibited.

The portfolio study compares the existing policy with the registered 20/40
entry/exit rank buffer. It reuses predictions and does not train a model.

After rolling-OOS design, freeze one to three gate-passing candidates:

```bash
.venv/bin/python -m qlib_platform --config configs/pipeline.yaml candidate-select \
  --contract-lock /path/to/phase2_contract_lock.json \
  --acceptance /path/to/candidate_acceptance.json \
  --design-release /path/to/design_release_manifest.json \
  --selection-date YYYY-MM-DD \
  --output /path/to/candidate_selection_lock.json
```

The final holdout is the next 252 sessions. It can be opened once, after label
maturity, and only from an append-only successor of the design DataRelease with
unchanged profile, PIT policy, and historical start. Opening emits a receipt;
it does not run or publish a strategy.
