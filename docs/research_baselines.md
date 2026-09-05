---
status: ACTIVE
owner: research
applies_to_commit: 3345aa189fc4ddf1f589671c30c42019cbc4e81e
last_verified: 2026-09-05
---

# Research baselines and templates

`qlib-platform` keeps reference controls separate from production-style research recipes. A research template selects a versioned AlphaPack, model profile, label contract and portfolio/backtest overlay while the quickstart still pins and verifies the same local immutable DatasetVersion.

## Qlib official Alpha158 control

Run the Microsoft Qlib Alpha158 LightGBM reference protocol against the actual stocks and prices in the active local DatasetVersion:

```bash
bash scripts/run_local_research.sh baseline
```

The equivalent explicit command is:

```bash
bash scripts/run_local_research.sh run --template qlib_alpha158_official_v1
```

Use `plan` before a long run when you want to inspect the generated overlay and child command:

```bash
bash scripts/run_local_research.sh plan --template qlib_alpha158_official_v1
```

The control intentionally preserves the qlib-platform data lineage, immutable DatasetVersion verification and research gates. It does **not** download or switch to Qlib's sample `cn_data` provider.

### Reference parity

| Contract | `qlib_alpha158_official_v1` |
| --- | --- |
| Feature handler | upstream `Alpha158` feature expressions |
| Infer processors | none, matching upstream Alpha158 |
| Learn processors | `DropnaLabel` + label `CSZScoreNorm` |
| Label | one-day T+1 close return (`return_1d_t1_v1`) |
| Model | LightGBM reference parameters from Qlib's Alpha158 benchmark |
| Boosting defaults | 1000 rounds, 50-round early stopping, made explicit |
| Portfolio | TopK 50, drop 5, minimum hold 1 |
| Tradability decision | `only_tradable: false`, matching the upstream strategy recipe |
| Account | 100,000,000 |
| Deal price | close |
| Fees | open 0.0005, close 0.0015, minimum 5 |
| Benchmark | SH000300, loaded from governed local benchmark data |
| Stocks/data | active local CSI300 membership, prices and DatasetVersion lineage |

Two deviations are deliberate. First, local research windows are selected from the active DatasetVersion instead of forcing Qlib's historical 2008-2020 sample dates onto a different dataset; explicit `--train/--valid/--test` windows remain available. Second, qlib-platform retains point-in-time local limit flags and its deterministic volume guard when replaying orders. This keeps the control executable against the real local A-share dataset rather than pretending the local market data has the exact mechanics of Qlib's tutorial provider.

## Platform Alpha158 baseline

The existing production-style baseline remains available:

```bash
bash scripts/run_local_research.sh run --template platform_alpha158_market_v1
```

It uses `alpha158_market_v1`, the platform LightGBM profile, production preprocessing, the platform execution contract and the existing promotion thresholds. Comparing this template with `qlib_alpha158_official_v1` isolates how much of a result comes from research-protocol choices rather than Alpha158 feature expressions themselves.

## Custom model experiments

A custom model profile now replaces the implicit default LightGBM preset when it is supplied by itself:

```bash
bash scripts/run_local_research.sh run \
  --alpha-pack alpha158_market_v1 \
  --model-profile configs/model_profiles/my_lightgbm_v2.yaml
```

Use both `--model` and `--model-profile` only when you intentionally want multiple jobs in the same research matrix.

Available templates, AlphaPacks and model presets are discoverable without training:

```bash
bash scripts/run_local_research.sh catalog
bash scripts/run_local_research.sh catalog --json
```

## Evidence and reports

Every `run`, `baseline`, `matrix` and `plan` output directory contains the same top-level evidence bundle:

```text
data/output/quickstart/<RUN>/
├── configs/
├── research_matrix.json
├── research_matrix.md
└── research_dashboard.html
```

The HTML dashboard is generated automatically. Child-process warnings that contain material warning, NaN, fallback or deprecation signals are persisted into the matrix and surfaced by the dashboard rather than disappearing after terminal output.

These templates are research controls, not exceptions to governance. A reference configuration does not weaken IC/RankIC stability gates, lineage checks, holdout isolation or promotion authorization.
