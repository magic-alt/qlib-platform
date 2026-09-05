---
status: ACTIVE
owner: research
applies_to_commit: 3345aa189fc4ddf1f589671c30c42019cbc4e81e
last_verified: 2026-09-05
---

# Research Web Dashboard

The research dashboard is a read-only reporting layer over existing qlib-platform evidence. It does not retrain models, change gates, rewrite DatasetVersion identity, or authorize candidate promotion.

## Automatic quickstart report

`run`, `baseline`, `matrix` and `plan` now render the dashboard automatically beside the matrix:

```text
data/output/quickstart/<RUN>/
├── research_matrix.json
├── research_matrix.md
└── research_dashboard.html
```

The final quickstart JSON prints the dashboard path. Material child-process warning lines are persisted into `research_matrix.json` and therefore appear in the HTML evidence instead of existing only in terminal stderr.

## What it shows

A dashboard combines the full local research path in one self-contained HTML file:

- pre-research environment: DatasetVersion, DataRelease lineage, verification evidence, mode/stage and runtime device;
- AlphaPack and model configuration, including the exact model-profile file and profile overrides;
- the reproducible child command used by quickstart;
- signal diagnostics: IC, RankIC, ICIR, RankICIR and long-short annualized return;
- promotion thresholds with explicit PASS / FAIL / N/A status;
- portfolio evidence such as Excess IR, drawdown, turnover and costs when those fields exist;
- feature/train/predict/portfolio timing and peak RSS;
- persisted data-quality warnings when they are present in the matrix;
- an automated interpretation and a recommended next research loop.

The HTML is self-contained: CSS is embedded and no CDN, JavaScript framework, analytics service, or network access is required.

## Re-render the latest research run

The explicit renderer remains useful for historical matrices or custom output paths.

macOS / Linux:

```bash
.venv/bin/python scripts/render_research_dashboard.py --latest
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe scripts\render_research_dashboard.py --latest
```

The renderer finds the newest `data/output/quickstart/*/research_matrix.json` and rewrites its sibling `research_dashboard.html`.

## Render a specific matrix

```bash
.venv/bin/python scripts/render_research_dashboard.py \
  data/output/quickstart/20260905T041510Z-run/research_matrix.json
```

Choose another output path when you want to archive or publish the report separately:

```bash
.venv/bin/python scripts/render_research_dashboard.py \
  data/output/quickstart/20260905T041510Z-run/research_matrix.json \
  --output data/output/reports/alpha158-market-lightgbm.html
```

The canonical Python entry point is also available:

```bash
.venv/bin/python -m qlib_platform.research.reporting.web_dashboard --latest
```

## Current Alpha158 Market + LightGBM snapshot

A curated snapshot of the 2026-09-05 baseline run is included at:

```text
docs/research_dashboard/current_alpha158_market_lightgbm_20260905.html
```

It captures the specific result discussed during baseline review:

- `IC = 0.0164`, `RankIC = 0.0417`;
- `ICIR = 0.0790`, `RankICIR = 0.2175`;
- long-short annualized return `17.51%`;
- benchmark annualized return `15.49%`;
- excess annualized return `-4.01%` before cost and `-6.68%` after cost;
- signal gate decision `REJECT` because the stability channel does not clear `0.50`;
- observed `$open` NaNs, future-calendar fallback and `Mean of empty slice` warnings;
- the recommended path from baseline matrix → richer Daily/PIT data → walk-forward → hypothesis-driven factors → cost stress.

The snapshot is explanatory evidence for that dated experiment. Future research should be rendered from its own `research_matrix.json` rather than overwriting the historical interpretation.

## Metric semantics

The dashboard intentionally distinguishes signal diagnostics from portfolio economics.

A positive RankIC or long-short diagnostic does not imply that a constrained TopK portfolio beats its benchmark. The reporting layer therefore keeps IC/RankIC beside, but separate from, portfolio Excess IR, drawdown, turnover and costs. Missing portfolio fields remain `N/A`; the renderer does not invent proxies.

Likewise, a `REJECT` gate is displayed as a research outcome, not a process failure. Thresholds are read from the current `ResearchThresholds` contract so the reporting layer does not maintain a second policy definition.
