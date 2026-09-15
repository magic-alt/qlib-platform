---
status: ACTIVE
owner: research
applies_to_commit: fc801c5f0132be6d00d466bd4d427187ef50a148
last_verified: 2026-09-15
---

# Governed research pipeline UX

`qlib-platform` uses one local research entry point for planning, matrix experiments, execution, recovery and review. The governed quickstart extends the existing CLI; it does **not** create a second scheduler and it does not require the web platform for offline research.

## Operating model

The user-facing lifecycle is:

`plan -> data verify -> materialize/features/split (owned by the selected research template) -> train/evaluate -> research backtest -> review -> permitted export`

Every `plan`, `run`, `matrix` and `baseline` request gets a canonical `researchId`. The identity includes the resolved DatasetVersion/DataRelease anchor, AlphaPack, full parsed model profile (including seed/model parameters), split windows, benchmark/portfolio inputs, artifact policy and verification/resource policy. Display paths, timestamps and runtime results are excluded from scientific identity.

Each matrix cell also gets a stable `cellId`. Adding or changing an unrelated cell does not change existing cell ids. Stage cache reuse therefore depends on the affected cell's scientific input hash rather than the whole matrix.

The run ledger is `<output>/run_state.json`. A stage is reusable only when its input hash is unchanged, its prior state is `SUCCEEDED`, and every recorded output still matches its SHA-256 hash. Interrupted, missing, half-written or modified output is not reused. Prior attempts remain in the ledger history. `<output>/.research.lock` prevents concurrent writers; writes to state/evidence use atomic replacement.

## Plan / read-only diagnosis

Plan first. It resolves and verifies the selected dataset but never trains a model or invokes `stability-diagnose`:

```bash
scripts/run_local_research.sh plan \
  --alpha-pack alpha158_market_v1 \
  --model lightgbm \
  --train 2016-01-04 2021-12-31 \
  --valid 2022-01-04 2022-09-23 \
  --test 2022-09-26 2025-05-08 \
  --max-concurrent-jobs 1 \
  --max-memory-gb 16 \
  --max-disk-gb 50
```

PowerShell uses the same arguments:

```powershell
.\scripts\run_local_research.ps1 plan --alpha-pack alpha158_market_v1 --model lightgbm --train 2016-01-04 2021-12-31 --valid 2022-01-04 2022-09-23 --test 2022-09-26 2025-05-08
```

The resulting `research_matrix.json` contains the full `researchSpec`, `researchId`, resolved dataset anchors, governance state, resource budget, per-job scientific input hash and exact child command. Unknown CLI parameters fail in argparse before a plan is produced.

## Change model

Use a built-in preset:

```bash
scripts/run_local_research.sh run --alpha-pack alpha158_market_v1 --model xgboost
```

Or a custom versioned profile. The complete YAML contents and SHA-256 are captured in the canonical manifest:

```bash
scripts/run_local_research.sh run \
  --alpha-pack alpha158_market_v1 \
  --model-profile configs/model_profiles/my_lightgbm_v2.yaml \
  --output data/output/quickstart/my-study
```

Changing a seed or one model parameter changes the corresponding scientific identity. For a one-parameter experiment, create two explicit profiles that differ only in that parameter and pass both profiles to `matrix`:

```bash
scripts/run_local_research.sh matrix \
  --alpha-pack alpha158_market_v1 \
  --model-profile configs/model_profiles/lgb_num_leaves_31.yaml \
  --model-profile configs/model_profiles/lgb_num_leaves_63.yaml \
  --output data/output/quickstart/leaves-study \
  --continue-on-error
```

This preserves both trials and failures; it does not silently keep only the winner.

## Experiment matrix

The default matrix remains Alpha158 Market/Daily/PIT x Ridge/LightGBM/XGBoost:

```bash
scripts/run_local_research.sh matrix \
  --output data/output/quickstart/alpha158-model-matrix \
  --continue-on-error
```

Restrict either axis explicitly when needed:

```bash
scripts/run_local_research.sh matrix \
  --alpha-pack alpha158_market_v1 \
  --alpha-pack alpha158_daily_v1 \
  --model ridge \
  --model lightgbm \
  --output data/output/quickstart/market-daily-ridge-lgb
```

`research_matrix.json`, `research_matrix.md` and `research_dashboard.html` remain the comparison surfaces. The matrix records coverage/runtime warnings, research manifests, portfolio manifests and the gate/status result for every cell.

## Failure recovery / resume

Rerun the **same command with the same `--output`**:

```bash
scripts/run_local_research.sh run \
  --alpha-pack alpha158_market_v1 \
  --model lightgbm \
  --output data/output/quickstart/my-study
```

Verified completed stages are reused. A killed process leaves the current stage incomplete; the next invocation increments its attempt and reruns it. If an artifact is edited, truncated or deleted, its hash check fails and that stage is rerun rather than accepted. A changed model profile, seed, split, label-bearing profile/config, DataRelease or portfolio input invalidates the corresponding scientific cache. Unaffected matrix-cell stages remain reusable when the caller intentionally keeps the same output directory.

## Holdout and Phase 3-D discipline

The current repository governance says formal candidates and model selection are disallowed, final holdout is sealed, and publishing is disabled. The governed planner reads this state and **fails closed** for release/walk-forward requests. Neither plan/UI/direct CLI is an authorization channel.

For example, under the current Phase 3-D state this is rejected before training:

```bash
scripts/run_local_research.sh plan --mode walk-forward --start 2022-01-01 --end 2025-12-31
```

Signal-stage research remains available on permitted train/internal-validation/OOS research windows. `stability-diagnose` is not invoked by `plan`, `run` or `matrix`; its existing explicit authorization rules remain separate. Changing governance in a future authorized phase must be done through the repository's governed `docs/current_state.md`, not by a convenience CLI flag.

## Resource and integration boundaries

Research jobs run serially (`--max-concurrent-jobs 1`) so the existing execution path remains authoritative. The plan records verification workers plus memory/disk budget declarations. Bundles and scientific identities do not depend on absolute output paths. In integrated mode the external platform still dispatches one research operation; this repository alone owns its internal stage ledger and lock. Existing outbox behavior remains the integration fallback, and integrity failures are never downgraded to success.
