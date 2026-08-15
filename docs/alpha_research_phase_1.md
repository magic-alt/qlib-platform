# Alpha Research Phase 1

## `alpha158_pit_v1` stability and failure-mechanism diagnosis

- **Status:** ACTIVE
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

Keep an explicit `Unclassified` bucket rather than forcing an unsupported economic label. Within
each group, cluster features using rank correlation and compare cluster representatives by OOS
stability, coverage, turnover, and net quantile spread. Across groups, measure incremental value
with orthogonalized or residualized group scores fitted without future data.

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

## Workstream 4: model explanation

Use XGBoost as the primary baseline and compare all models on identical OOS keys. Produce:

- permutation importance on validation/OOS-safe partitions;
- gain/split importance as model diagnostics, not causal evidence;
- SHAP summaries and stability by fold/year/regime;
- leading pairwise interactions and their stability;
- bounded depth and regularization sensitivity around the current profile.

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

Test a bounded grid for TopK, `n_drop`, hold period, score weighting, volatility scaling, industry
constraints, single-name caps, and turnover penalties. Treat these as portfolio experiments, not
new model evidence. Report sensitivity surfaces and stable regions instead of selecting one lucky
point.

## Required artifacts

Phase 1 should publish one immutable diagnosis bundle containing:

- `feature_catalog.csv` with economic group and feature definition;
- `feature_stability.parquet` and `feature_stability_summary.csv`;
- `feature_correlation.parquet` and `feature_clusters.json`;
- `single_factor_quantiles.parquet` and `turnover_attribution.parquet`;
- `regime_definitions.json` and `regime_diagnostics.parquet`;
- `model_comparison.parquet`, `feature_importance.parquet`, and SHAP/interaction summaries;
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
