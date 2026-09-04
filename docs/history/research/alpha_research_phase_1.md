---
status: HISTORICAL
owner: research
applies_to_commit: 4f5c5d5
last_verified: 2026-08-28
superseded_by: alpha_research_phase_2.md
---

# Alpha Research Phase 1

## `alpha158_pit_v1` stability and failure-mechanism diagnosis

- **Status:** COMPLETED (historical reference)
- **Infrastructure prerequisite:** `Research Infrastructure: CERTIFIED`
- **Primary baseline:** XGBoost
- **Comparators:** Ridge and LightGBM

## Objective

This phase explains the current research results before feature-pack redesign or broad
hyperparameter search. It must answer:

1. Which features have stable, repeatable predictive value?
2. Why does XGBoost have materially better IC/RankIC than Ridge and LightGBM while remaining at
   `RESEARCH_REVIEW` rather than reaching the production Research Gate?
3. Is Alpha lost mainly in prediction quality, regime drift, or portfolio implementation through
   turnover and costs?

The goal is diagnosis and a defensible next decision, not immediate return maximization.

## Frozen experimental contract

All primary comparisons must pin the same:

- DataRelease and governed trading calendar;
- `alpha158_pit_v1` FeatureSnapshot;
- label specification and decision/realization timing;
- rolling and final-holdout split specification;
- universe and cross-sectional eligibility rules;
- transaction-cost assumptions and benchmark;
- random seeds and model profiles;
- PredictionSnapshot identity and payload checksum for portfolio-only comparisons.

The final holdout remains sealed for research selection. Phase 1 selection and diagnosis use
rolling OOS evidence; the holdout is used only by the existing locked final evaluation protocol.

## Feature-diagnostics foundation

The first implementation is an independent, read-only Research Study Layer under
`qlib_platform.research`. It consumes the certified raw FeatureSnapshot and the rolling OOS labels
already embedded in the aggregate PredictionSnapshot. It does not call feature materialization,
fit or replay a model, execute a portfolio, authorize publishing, or read final-holdout payloads.

Run it with the repository-local interpreter:

```bash
.venv/bin/python -m qlib_platform --config configs/pipeline.yaml alpha-diagnose \
  --acceptance <FULL_WALK_FORWARD_ACCEPTANCE_JSON> \
  --walk-forward <CERTIFIED_XGBOOST_WALK_FORWARD_BUNDLE> \
  --feature-snapshot <FEATURE_SNAPSHOT_DIRECTORY> \
  --taxonomy configs/alpha_taxonomy/alpha158_pit_v1.yaml \
  --output <STUDY_OUTPUT_ROOT>
```

The acceptance and XGBoost bundle are both required. The acceptance proves cross-model Golden
Acceptance but deliberately contains no local run paths; the XGBoost bundle provides the certified
rolling labels, fold plan, selection lock, and FeatureSnapshot manifest checksum. The command
cross-validates both sources and fails closed on identity or checksum drift.

The immutable output is written under `ars_<contract-sha256>/`. Its contract binds the
DataRelease, dataset version, AlphaPack, FeatureSnapshot, LabelSpec, rolling split, acceptance,
taxonomy, implementation hashes, and diagnostic parameters. Rerunning the same contract validates
and reuses byte-exact artifacts; it never overwrites a corrupt study.

Feature diagnostics use a minimum valid cross-section of 50. IC and RankIC are NaN for smaller or
constant cross-sections. ICIR retains the existing unannualized Qlib-compatible daily mean/std
definition, while separate Newey-West t-statistics use the LabelSpec lookahead as the Bartlett lag.
Trailing 63- and 252-session statistics are causal windows ending at the reported date.
The summary artifact retains raw `rank_icir` and adds `oriented_rank_icir`; any report column paired
with `oriented_rank_ic_mean` must display `oriented_rank_icir`, so declared negative directions do
not appear with a semantically contradictory IR sign.

Single-factor quantiles preserve both raw Q5-Q1 and taxonomy-oriented spreads. Unknown directions
are not flipped from observed OOS outcomes. Membership turnover is the fraction of the prior
quantile membership absent from the current quantile. Pairwise redundancy uses the mean of daily
cross-sectional rank correlations; deterministic Union-Find edges require the same economic family,
`role=alpha`, and absolute correlation of at least 0.85.

## Workstream 1: feature stability

For every feature, compute from OOS observations only:

- daily Pearson IC and Spearman RankIC;
- mean, standard deviation, IR, positive-day ratio, and effective observation count;
- calendar-year statistics and rolling 12-month statistics;
- daily cross-sectional coverage, missingness, non-finite rate, and dispersion;
- universe, industry, and size-bucket coverage where the governed metadata permits it;
- single-factor quantile returns, monotonicity, long-short spread, turnover, and net spread;
- contribution to score and portfolio turnover, reported separately from predictive strength.

Report uncertainty and sample size with every stability statistic. Never rank features solely by
full-period mean IC.

## Workstream 2: redundancy and factor taxonomy

Map features into the initial economic groups:

- Momentum
- Value
- Quality
- Growth
- Liquidity
- Volatility
- Reversal
- Size
- Flow
- TechnicalOther
- StateSupport

Every feature has an explicit `role` (`alpha`, `exposure`, or `support`) and economic direction
(`positive`, `negative`, or `unknown`). `TechnicalOther` is used instead of inventing an unsupported
economic interpretation. Money-flow features use the separate `Flow` family. `LOG_CIRC_MV` is a
Size exposure; `CIRC_MV`, `MONEY20`, PAUSED/ST/listed/limit fields remain available for eligibility
logic but are support-only and excluded from alpha ranking and clusters. Within each alpha family,
cluster features using rank correlation and compare cluster representatives by OOS stability,
coverage, turnover, and net quantile spread. Across groups, measure incremental value with
orthogonalized or residualized group scores fitted without future data.

This workstream produces candidates for `alpha158_pit_v2` or `multifactor_core_v2`; it does not
change the registered AlphaPack during Phase 1.

## Workstream 3: regime analysis

Define regimes using only information available at each decision time. At minimum, examine:

- market trend;
- realized volatility;
- trading activity and liquidity;
- large-cap versus small-cap style;
- industry breadth or dispersion.

Regime boundaries must be predeclared or estimated inside each training window. For every regime,
compare feature groups and Ridge/LightGBM/XGBoost on IC, RankIC, IR, positive-fold ratio, coverage,
turnover, gross return, costs, and net return. Include regime sample sizes and transition periods so
that a small or dominant regime cannot masquerade as robust evidence.

The central test is whether XGBoost's advantage comes from stable nonlinear signal, a small set of
interactions, or concentration in favorable regimes, and whether the high positive-IC-fold ratio
is diluted by low magnitude or high variance across time.

The causal regime engine is a second immutable, read-only study layered on a PASS feature study.
It validates the three accepted rolling OOS prediction checksums and consumes no final-holdout
artifact. Run it with:

```bash
.venv/bin/python -m qlib_platform --config configs/pipeline.yaml regime-diagnose \
  --base-study <ALPHA_PHASE1_FEATURE_MANIFEST> \
  --acceptance <FULL_WALK_FORWARD_ACCEPTANCE_JSON> \
  --walk-forward <CERTIFIED_XGBOOST_WALK_FORWARD_BUNDLE> \
  --ridge-predictions <CERTIFIED_RIDGE_ROLLING_OOS_PARQUET> \
  --lightgbm-predictions <CERTIFIED_LIGHTGBM_ROLLING_OOS_PARQUET> \
  --feature-snapshot <FEATURE_SNAPSHOT_DIRECTORY> \
  --taxonomy configs/alpha_taxonomy/alpha158_pit_v1.yaml \
  --regimes configs/regimes/ashare_regime_v1.yaml \
  --output <REGIME_STUDY_OUTPUT_ROOT>
```

`ashare_regime_v1` predeclares five independent one-dimensional labels: benchmark trend,
benchmark realized volatility, cross-sectional market activity, lagged-size small-minus-large
style, and PIT SW2021 L1 industry breadth. Expanding volatility/activity/breadth thresholds for
date T use observations only through T-1. Size baskets use a one-session-lagged size rank. The
first diagnostic family is fixed to eight stable candidates, three direction-unknown MIN
hypotheses, and the accepted Ridge/LightGBM/XGBoost predictions. States with fewer than 63 sessions
are `INSUFFICIENT_SAMPLE`; feature-regime inference uses Newey-West HAC and one global BH-FDR
adjustment across the predeclared factor-regime tests.

If a certified input component is absent, the affected dimension is `INPUT_UNAVAILABLE` and the
study status is `PARTIAL`. In particular, current-industry fields from a security master must never
substitute for a missing PIT industry component. A named fold such as `rolling_07` is profiled only
after the same causal rules label all rolling OOS dates; it is not itself a regime definition.

The regime bundle contains `regime_definitions.json`, `regime_labels.parquet`, factor and model
regime tables, model-to-core-composite correlation, TopK Jaccard overlap, fold regime profiles, and
a manifest/report. It does not produce AlphaPack v2 or a final Phase 1 recommendation.

## Workstream 4: model explanation

Use XGBoost as the primary baseline, LightGBM as the nonlinear comparator, and Ridge coefficients
as a linear reference on identical rolling OOS keys. The explanation study runs only after the
feature, regime, and failure-attribution studies pass their own integrity contracts:

```bash
.venv/bin/python -m qlib_platform --config configs/pipeline.yaml explanation-diagnose \
  --base-study <ALPHA_PHASE1_FEATURE_MANIFEST> \
  --regime-study <REGIME_STUDY_MANIFEST> \
  --attribution-study <ATTRIBUTION_STUDY_MANIFEST> \
  --acceptance <FULL_WALK_FORWARD_ACCEPTANCE_JSON> \
  --ridge-walk-forward <CERTIFIED_RIDGE_WALK_FORWARD_BUNDLE> \
  --lightgbm-walk-forward <CERTIFIED_LIGHTGBM_WALK_FORWARD_BUNDLE> \
  --xgboost-walk-forward <CERTIFIED_XGBOOST_WALK_FORWARD_BUNDLE> \
  --feature-snapshot <FEATURE_SNAPSHOT_DIRECTORY> \
  --model-artifact-root <LOCAL_MLFLOW_ROOT> \
  --taxonomy configs/alpha_taxonomy/alpha158_pit_v1.yaml \
  --explanation configs/explanation/ashare_model_explanation_v1.yaml \
  --output <EXPLANATION_STUDY_OUTPUT_ROOT>
```

The original Full Walk-forward Acceptance hashes aggregate predictions, not the fold-local
`params.pkl` binaries retained by the Qlib/MLflow recorder. The explanation study therefore fails
closed unless it can bind every rolling model retrospectively through the exact recorder id,
recorder `pred.pkl`, component and aggregate accepted predictions, fitted processor-state replay,
model score parity, and TreeSHAP additivity. This evidence grade is
`DERIVED_SAME_RECORDER_ADDITIVITY`; it must not be described as direct binary certification by the
original Full Acceptance.

The study produces:

- gain/split importance as model diagnostics, not causal evidence;
- Ridge signed/absolute coefficient reference;
- native TreeSHAP summaries and stability by fold/year/causal regime;
- leading pairwise interactions and their stability;
- explicit H1/H2/H3 hypothesis assessments and one XGBoost mechanism classification.

The immutable bundle contains `feature_importance.parquet`, `shap_summary.parquet`,
`shap_by_fold.parquet`, `shap_by_year.parquet`, `shap_by_regime.parquet`,
`xgb_interactions.parquet`, `explanation_stability.parquet`, a summary, report, and manifest.
Missing PIT industry breadth propagates as `regimeConditioning=PARTIAL` without preventing the core
model explanation from passing.

This retrospective study performs zero training, creates no new PredictionSnapshot, executes no
portfolio, accesses no final-holdout component, and authorizes no publishing. Native SHAP and
interaction calculations are recorded separately as explanation evaluations; they are not hidden
as zero model computation. Permutation importance and retraining-based bounded sensitivity remain
`NOT_RUN` in this no-retraining study and cannot be used as positive evidence for XGBoost tuning.

Do not start a large hyperparameter sweep or introduce a broad deep-model family in this phase.
Any tuning experiment must have a written hypothesis and a small, fixed search budget.

## Workstream 5: prediction-to-portfolio attribution

Use the same immutable PredictionSnapshot to isolate portfolio choices without retraining. Build an
attribution bridge with these stages:

1. raw daily score and label diagnostics;
2. gross quantile or TopK signal return;
3. realized holdings under TopK, `n_drop`, and holding rules;
4. gross portfolio return;
5. turnover and transaction-cost drag;
6. net excess return and risk metrics.

The first failure-attribution study deliberately narrows this to the accepted XGBoost baseline,
`TopK20/drop5/hold5`, and `TopK50/drop10/hold5`. It does not select a winner. The baseline continuous
rolling OOS portfolio is read from the certified XGBoost walk-forward bundle; optional comparator
baseline and bounded prediction-only portfolio manifests use the exact accepted prediction checksum.
Run it with:

```bash
.venv/bin/python -m qlib_platform --config configs/pipeline.yaml attribution-diagnose \
  --regime-study <REGIME_STUDY_MANIFEST> \
  --acceptance <FULL_WALK_FORWARD_ACCEPTANCE_JSON> \
  --walk-forward <CERTIFIED_XGBOOST_WALK_FORWARD_BUNDLE> \
  --ridge-predictions <CERTIFIED_RIDGE_ROLLING_OOS_PARQUET> \
  --lightgbm-predictions <CERTIFIED_LIGHTGBM_ROLLING_OOS_PARQUET> \
  --portfolio-run xgboost:topk20=<PREDICTION_ONLY_MANIFEST> \
  --portfolio-run xgboost:topk50=<PREDICTION_ONLY_MANIFEST> \
  --output <ATTRIBUTION_STUDY_OUTPUT_ROOT>
```

`--portfolio-run` is optional and repeatable. It also accepts `ridge:baseline` and
`lightgbm:baseline` when their certified portfolio bundles are available. Every supplied strategy
must match one of the three predeclared variants in
`configs/attribution/ashare_failure_attribution_v1.yaml`.

Signal conversion uses the five-session forward label and reports IC, RankIC, TopK and BottomK
label means, TopK-universe and TopK-BottomK spreads, hit rate, temporal TopK overlap, rank turnover,
prediction dispersion, and XGBoost/comparator TopK overlap. Realized P&L remains a separate daily
chain: gross return, benchmark, gross excess, explicit cost, net return, net excess, IR, drawdown,
and turnover. The two chains are never added into a statistically invalid waterfall.

The study reuses `strategy_audit.parquet` fields and attributes decision and execution events to
entry, exit, rank replacement, hold threshold, suspension, price-limit block, unfilled, and partial
fill categories. It reports the full OOS sample, every causal regime, and `rolling_07`. Cost
sensitivity is fixed to `0x`, `0.5x`, `1x`, `1.5x`, and `2x` on the same realized gross-return path.

The immutable bundle derives exactly one `PRIMARY_ALPHA_LOSS_SOURCE` from `SIGNAL`, `MODEL`,
`RANKING`, `PORTFOLIO`, `COST`, `REGIME`, or `MIXED`. The classification contract is predeclared;
it is not edited after seeing the study result. The command performs zero model training, model
prediction, feature materialization, portfolio backtest execution, final-holdout selection, or
publishing.

Broader score weighting, volatility scaling, industry constraints, single-name caps, and turnover
penalties remain follow-on portfolio research only when this attribution identifies implementation
as the primary loss source.

## Workstream 6: Phase 1 synthesis

The synthesis study is the terminal, read-only decision layer. It accepts only the four immutable
feature, regime, attribution, and explanation manifests; validates every referenced artifact hash
and the complete parent/identity chain; and derives one recommendation from thresholds frozen in
`configs/synthesis/ashare_phase1_synthesis_v1.yaml`:

```bash
.venv/bin/python -m qlib_platform --config configs/pipeline.yaml phase1-synthesize \
  --feature-study <ALPHA_PHASE1_FEATURE_MANIFEST> \
  --regime-study <REGIME_STUDY_MANIFEST> \
  --attribution-study <ATTRIBUTION_STUDY_MANIFEST> \
  --explanation-study <EXPLANATION_STUDY_MANIFEST> \
  --synthesis configs/synthesis/ashare_phase1_synthesis_v1.yaml \
  --output <SYNTHESIS_OUTPUT_ROOT>
```

Recommendation priority is fixed to Portfolio Construction, Regime-aware Research, XGBoost
Tuning, AlphaPack v2, then No-go/New Alpha. Each candidate records eligibility, supporting and
counter-evidence, gaps, and rejection reasons. `XGBOOST_TUNING` is ineligible when bounded
sensitivity is `NOT_RUN`; importance or SHAP evidence alone is not a tuning mandate.

The current PIT industry gap is the only permitted non-blocking `PARTIAL` regime condition. The
bundle retains `evidenceCompleteness=PARTIAL`, `regimeDiagnostics=PARTIAL`, and
`DATA_GAP_PIT_INDUSTRY`, while setting
`phase1Completion=COMPLETE_WITH_KNOWN_DATA_GAP`. Missing industry evidence is excluded from
denominators and is never treated as zero or negative performance. Any other missing regime,
lineage mismatch, model-binding/additivity failure, artifact tamper, final-holdout selection, or
publishing authorization fails closed.

The synthesis bundle stores byte-exact source manifest receipts, a checksum-backed artifact index,
the evidence summary, report, and `alpha_phase_1_manifest.json`. It copies the actual upstream
artifact names instead of inventing legacy aliases. It performs zero training, prediction,
materialization, or portfolio backtests and does not authorize publishing.

## Required artifacts

Phase 1 should publish one immutable diagnosis bundle containing:

- `factor_taxonomy.json` with economic group and feature definition;
- `feature_daily_ic.parquet`, `feature_summary.parquet`, and fold/year/rolling stability tables;
- `feature_correlation.parquet` and `feature_clusters.json`;
- `factor_quantile_returns.parquet` and `turnover_attribution.parquet`;
- `regime_definitions.json`, `factor_regime_diagnostics.parquet`, and
  `model_regime_diagnostics.parquet` plus correlation/overlap/fold profiles;
- `feature_importance.parquet`, `shap_summary.parquet`, fold/year/regime SHAP tables, and
  `xgb_interactions.parquet`;
- `prediction_portfolio_attribution.parquet` and portfolio sensitivity results;
- `alpha_phase_1_manifest.json` with all governed identities, code commit, parameters, and hashes;
- `alpha_phase_1_report.md` with conclusions, uncertainty, rejected hypotheses, and recommendation.

Generated data and large artifacts remain under the untracked `data/` output hierarchy. The code,
schemas, configs, tests, and a small deterministic fixture belong in the repository.

## Decision rules

The report must end with exactly one primary recommendation:

- **AlphaPack v2** when stable feature/group evidence exists but redundancy or unstable features
  dilute it;
- **XGBoost tuning** when stable signal exists and bounded model sensitivity shows recoverable
  underfit or overfit;
- **Portfolio Construction** when score diagnostics are stable but implementation, turnover, or
  costs consume most of the gross Alpha;
- **Regime-aware research** when performance is repeatable within sufficiently populated regimes
  but unstable in aggregate;
- **No-go / rethink Alpha** when predictive evidence is too weak or too uncertain after accounting
  for redundancy and multiple testing.

Simple `Ridge + LightGBM + XGBoost` score/rank ensembles are a follow-on experiment only after the
individual-model diagnosis is complete. Dynamic rolling-IC weighting follows only if the simple
ensemble improves stability without relying on the final holdout; stacking is out of scope.

## Completion criteria

Phase 1 is complete when:

- all required diagnostics are reproducible from pinned inputs;
- feature and model statistics are OOS and include coverage and uncertainty;
- regime definitions are causal and sample-size qualified;
- prediction loss and portfolio/cost loss are quantitatively separated;
- no final-holdout information influenced a research choice;
- the report answers all three objective questions and selects one next action;
- deterministic tests cover metric definitions, temporal alignment, and artifact contracts.

Completion does not require a model to pass the Research Gate.
