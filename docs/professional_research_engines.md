---
status: ACTIVE
owner: research
applies_to_commit: 463400ec69d4ffc721a832703ce1797f46f9c7f6
last_verified: 2026-09-05
---

# Professional research engines

This page describes the professional research engines introduced by the initial P0 work. These
capabilities extend the research surface; they do **not** change the active
governance state in `docs/current_state.md`.

## 1. Validation-only HPO / Experiment Engine

`qlib_platform.research.workflow.hpo` introduces versioned `StudySpec`,
`SearchSpace`, `SearchParameter`, trial evidence and an Optuna-backed study
runner.

A study identity binds:

- immutable `DatasetVersion`;
- immutable `FeatureSnapshot`;
- model family, model profile and profile fingerprint;
- full base-model parameter fingerprint and persisted base parameters;
- Git commit and dirty state;
- search-space fingerprint;
- objective metric, direction and random seed.

The selection segments are hard-coded to `train` + `valid`. A caller cannot
configure `test` or `final_holdout` as an HPO selection segment. For Qlib
models whose `predict()` implementation is hard-coded to the `test` segment,
the built-in validation objective creates a restricted DatasetH where the
`test` alias points to the **validation** dates. The original test/final
holdout range is never copied into that view.

Every trial records parameters, validation metrics, selection segments and
`holdoutAccessed=false`. The study manifest also records that it created no
formal candidate and authorized no promotion.

Example search-space configuration:

```yaml
parameters:
  learning_rate: {type: float, low: 0.005, high: 0.10, log: true}
  num_leaves: {type: int, low: 16, high: 128}
  lambda_l2: {type: float, low: 0.001, high: 200.0, log: true}
```

The maintained baseline is `configs/hpo/lightgbm_validation_v1.yaml`.
Install with `pip install -e '.[hpo]'`. CI/dev pins Optuna 4.9.0. The runner is
exposed as a Python research API while the current Phase 3-D governance state continues to
disallow model selection; the presence of the engine does not authorize an HPO
run.

## 2. Qlib Model Zoo adapters

The unified model registry now includes:

| Family | Upstream Qlib implementation | Research dataset |
| --- | --- | --- |
| `qlib_lstm` | `pytorch_lstm_ts.LSTM` | 20-session `TSDatasetH` view |
| `qlib_gru` | `pytorch_gru_ts.GRU` | 20-session `TSDatasetH` view |
| `qlib_transformer` | `pytorch_transformer_ts.TransformerModel` | 20-session `TSDatasetH` view |
| `qlib_tcn` | `pytorch_tcn_ts.TCN` | 20-session `TSDatasetH` view |
| `qlib_tabnet` | `pytorch_tabnet.TabnetModel` | existing `DatasetH` |
| `qlib_double_ensemble` | `double_ensemble.DEnsembleModel` | existing `DatasetH` |

The temporal wrapper reuses the exact handler and segment boundaries produced
by qlib-platform and changes only the dataset *view* to Qlib's native
`TSDatasetH`. `d_feat` is bound to the actual pinned feature width; a profile
that tries to silently use a different width fails closed. This ensures model
comparison does not silently change the AlphaPack/FeatureSnapshot.

Profiles are under `configs/model_profiles/qlib_*_v1.yaml`. They are usable
through the existing research CLI, for example:

```bash
tq-research run --alpha-pack alpha158_market_v1 \
  --model-profile configs/model_profiles/qlib_lstm_v1.yaml
```

LSTM, GRU, Transformer, TCN and TabNet require the optional `pytorch` extra;
DoubleEnsemble uses the existing Qlib/LightGBM stack. Upstream Qlib PyTorch models
support CUDA/CPU; Apple MPS is deliberately not reported as supported because the upstream implementations choose devices with
`torch.cuda.is_available()`.

These six adapters are currently **research-only**. Portable live
`MODEL_RELEASE` bundles need a model-specific sequence/preprocessing contract;
attempting production-bundle save/refit fails closed rather than serializing a
model that cannot reproduce research inference.

## 3. Factor Evaluation Engine

`qlib_platform.research.features.registry` defines immutable factor
definitions with a predeclared family, role and direction. The direction must
be `+1` or `-1`; the engine never chooses the sign from validation results.

`qlib_platform.research.features.evaluation` evaluates a registered factor
panel with:

- coverage;
- daily IC and RankIC;
- ICIR and RankICIR;
- cross-sectional exposure neutralization by OLS residualization;
- rank turnover;
- explicit multi-horizon decay labels;
- mean daily factor RankIC correlation;
- deterministic correlation clustering;
- incremental RankIC versus a supplied baseline signal;
- policy-driven `ADMIT` / `REJECT` screening.

Correlation pruning is deterministic: candidates are ordered by pre-oriented
validation RankICIR, and a later candidate is rejected when its absolute mean
daily RankIC correlation with an already-admitted factor reaches the policy
threshold.

`write_factor_evaluation()` binds evidence to `DatasetVersion`,
`FeatureSnapshot`, registry hash and Git revision. `ADMIT` / `REJECT` is
**screening evidence only**. It does not create a formal candidate, authorize
Phase 3 model/factor selection, open the final holdout, or publish a portfolio.

## Validation and next integration boundary

These engines deliberately stop before the execution plane and before production
deployment of sequence models. The HPO engine remains intentionally API/config
driven until the active research program authorizes model selection. The next
integration should add an authorization-aware CLI entry point, then OOF ensemble
research without weakening the sealed-holdout contract.
