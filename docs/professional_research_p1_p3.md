# Professional research expansion: P1-P3

This change adds the next research-platform layers without altering the active governed Phase 3-D program. It does not select/promote a model, access the final holdout, or authorize publishing.

## Architecture

```text
Base model OOF predictions ─┐
                            ├─ average / weighted / temporal stacking ── alpha vector
Qlib DEnsembleModel ────────┘                                      │
                                                                  ▼
Historical returns + style + industry ── Barra-like risk model ── optimizer
                                                                  │
                                                                  ▼
                                                        target portfolio
                                                                  │
                                                                  ▼
                                         A-share realistic simulator
                                                                  │
                         ┌────────────────────────────────────────┴──────────────┐
                         ▼                                                       ▼
                 immutable artifacts                                  experiment metadata
                                                                            DuckDB / PostgreSQL
                                                                                  │
                                                                                  ▼
                                                                        read-only web console
```

Parallel execution stays outside the default path. Serial execution remains the default; process, Ray and Dask backends are opt-in and should only be enabled after a benchmark demonstrates that study size amortizes scheduler/serialization overhead.

## P1: Ensemble research

`qlib_platform.research.evaluation.ensemble` and `evaluation.stacking` provide:

- arithmetic averaging with exact-population validation;
- normalized non-negative weighted blending;
- linear ridge stacking fitted only from rows explicitly marked `is_oof=true`;
- temporal provenance validation requiring `fit_end < prediction_time`;
- meta-level `temporal_cross_fit_predict`, which trains each meta fold only from earlier OOF folds;
- a lazy adapter for Qlib `DEnsembleModel` (`qlib.contrib.model.double_ensemble`).

Base-model OOF predictions alone are not enough: fitting one meta learner on every OOF row and reporting predictions on those same rows makes the meta layer in-sample. The first fold(s) therefore remain unscored until enough earlier OOF folds exist.

## P1: Portfolio optimizer and risk model

`qlib_platform.research.portfolio.risk_model` supports sample covariance with shrinkage/PSD repair, standardized style exposures, one-hot industry exposures, daily cross-sectional factor-return estimation, factor covariance, specific variance and reconstructed asset covariance.

`qlib_platform.research.portfolio.optimizer` solves the research objective:

```text
maximize alpha'w
       - risk_aversion * w'Cov*w
       - linear_cost * |w - w0|
       - impact_cost * (w - w0)^2
```

Constraints include target exposure, box weights, maximum turnover and arbitrary style/industry exposure bounds. The deterministic projected-gradient core uses NumPy, so SciPy/CVXPY is not required for the standard path. Infeasible constraints fail closed rather than being silently relaxed.

The versioned example profile is `configs/portfolio/alpha_optimizer_ashare_v1.yaml`.

## P1: A-share realistic simulator

`qlib_platform.backtesting.ashare_simulator` is a research simulator, not an OMS. It models:

- T+1 position availability;
- suspension and zero-volume rejection;
- authoritative daily limit-up/limit-down and locked-limit fields;
- fallback board/ST/IPO price-limit inference when authoritative fields are unavailable;
- buy-lot rounding;
- cumulative daily participation caps and partial fills;
- cash/position availability;
- half-spread, slippage and square-root participation impact;
- commissions, transfer fee and sell-side stamp tax;
- per-order capacity and aggregate capacity-utilization diagnostics.

For historical research, provide exchange/data-vendor daily limit fields whenever possible. Board-rule inference intentionally remains a fallback because price-limit and IPO regimes change over time.

The simulator emits only simulated fills, rejections, account marks and research diagnostics. Broker submission, broker state, order replacement and live execution remain in the execution-plane repository.

## P2: Experiment database and web research console

`qlib_platform.research.evidence.experiment_store.ExperimentStore` stores searchable metadata for experiments/lineage, scalar metrics, models, factors, portfolios and artifact URI/checksum references. Large immutable artifacts remain in the existing artifact store.

DuckDB is the local-first default. PostgreSQL is optional through the `postgres` extra.

Start the read-only console with:

```bash
.venv/bin/python -m qlib_platform.research.reporting.research_console --db research_experiments.duckdb
```

or:

```bash
tq-research-console --db research_experiments.duckdb --host 127.0.0.1 --port 8765
```

The console lists experiments/models/factors/portfolios. Comparison views use `/?compare=exp_a,exp_b`, `compare_models=...`, `compare_factors=...`, and `compare_portfolios=...`. Read-only JSON endpoints are exposed under `/api/` for future richer front ends.

## P3: Parallel research, deliberately opt-in

`qlib_platform.research.workflow.parallel_executor` defines one research executor interface with:

- `serial` (default);
- `process` (standard-library local CPU parallelism);
- `ray` (optional `parallel-ray` extra, CPU/GPU resource hints);
- `dask` (optional `parallel-dask` extra).

`ParallelizationPolicy` requires both study size and estimated total CPU work to clear configured thresholds before distributed execution is justified. `benchmark_executor` records actual throughput before changing the default backend.

Ray/Dask are not imported unless selected. GPU resources are exposed as Ray resource hints; this change does not imply that arbitrary Qlib workloads become GPU-accelerated merely because a distributed scheduler is present.

## Validation invariants

Tests cover mismatched ensemble populations, OOF/temporal leakage, temporal meta cross-fit, PSD covariance, optimizer box/turnover/factor constraints, A-share T+1, partial fills, locked limits, transaction costs/impact/capacity, experiment catalog round-trips and comparison, console rendering, and serial-default parallel execution.
