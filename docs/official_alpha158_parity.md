# Official Alpha158 / LightGBM parity lane

Issue #130 adds a control experiment that is intentionally isolated from the normal qlib-platform research program. It answers one question only: whether an immutable A-share DatasetVersion built from a frozen Tushare release can reproduce the Microsoft Qlib Alpha158/LightGBM benchmark within a tolerance registered before the local result is inspected.

This lane is **not** `alpha158_pit_v1`, does not use the platform `forward_5d` label, does not inherit the production research gate, and does not authorize candidate selection, publishing, deployment or trading.

## Frozen protocol

The profile is `configs/research/qlib_official_alpha158_lgb_v1.yaml`. Its workflow is an exact frozen copy of Microsoft Qlib `examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml` at commit `79633dd9506ea689e5400dea0197717b5b3d74b7`. The workflow SHA256 is `0cbe1f729f43a2b8cd735759e6244c2e9f8db967555bfe24a7143fce4605d680`.

The frozen experiment uses CSI300 / SH000300, native `qlib.contrib.data.handler.Alpha158`, train `2008-01-01..2014-12-31`, validation `2015-01-01..2016-12-31`, test/backtest `2017-01-01..2020-08-01`, the upstream LightGBM kwargs, and `TopkDropoutStrategy(topk=50, n_drop=5)`. Backtest semantics remain `limit_threshold=0.095`, `deal_price=close`, `open_cost=0.0005`, `close_cost=0.0015`, and `min_cost=5`.

At execution time the only workflow field rewritten is `qlib_init.provider_uri`, which is bound to the resolved immutable DatasetVersion. If the checked-in workflow changes without an explicit profile/hash change, planning fails closed.

## Preregistered comparison contract

The official LightGBM / Alpha158 / CSI300 README means frozen in the profile are IC `0.0448`, ICIR `0.3660`, RankIC `0.0469`, RankICIR `0.3877`, annualized excess return with cost `0.0901`, information ratio `1.0164`, and max drawdown `-0.1038`.

The first four signal metrics are primary. Portfolio metrics are secondary. `vendor_tolerances` are versioned profile data and must not be changed after inspecting a Tushare result. A miss remains `FAIL`; the report assigns an attribution category (`universe`, `data`, `feature`, `model`, or `backtest`) and does not tune Alpha158, labels, LightGBM parameters, portfolio costs, or strategy settings to make the comparison pass.

Engine parity is a separate control. On the same provider, the harness runs Qlib's native `qlib.cli.run.workflow` and qlib-platform's `run_qrun` compatibility path and compares official recorder metrics, predictions, recorder artifact schema, and portfolio report using the much tighter `engine_tolerances` in the profile.

## Required immutable input

Use an explicit DatasetVersion or immutable registry reference produced from a frozen Tushare DataRelease. Legacy/unversioned Qlib directories are rejected. The DatasetVersion manifest must bind the DataRelease and preserve content hashes, universe membership identity and conversion lineage.

The plan also checks:

- calendar coverage for the official 2008-2020 protocol;
- exchange-prefixed symbol mapping and historical CSI300 membership intervals;
- benchmark `SH000300` provider availability;
- sample OHLCV/adjustment-factor provider fields;
- DataRelease/DatasetVersion hashes and conversion lineage;
- content-addressed DatasetVersion partitions.

The parity command consumes an already-frozen DatasetVersion and therefore does not need a live `TUSHARE_TOKEN`. Credentials must remain in environment/secrets during the separate ingestion step and never appear in the parity profile, plan or report.

## Manual golden checks

Before a real vendor result can pass, provide a YAML evidence file with at least two CSI300 rebalance checks and two corporate-action/adjustment checks. Evidence should identify the date/symbol and the external source or manual calculation used to verify the point-in-time result.

```yaml
checks:
  - kind: csi300_rebalance
    passed: true
    evidence: "2018-06 rebalance: membership interval compared with provider snapshot ..."
  - kind: csi300_rebalance
    passed: true
    evidence: "2019-12 rebalance: membership interval compared with provider snapshot ..."
  - kind: corporate_action
    passed: true
    evidence: "600000.SH dividend/split date: raw adj_factor and normalized Qlib factor checked ..."
  - kind: corporate_action
    passed: true
    evidence: "000001.SZ second action date: close/factor continuity checked ..."
```

Place the evidence outside generated DatasetVersion directories, for example `artifacts/official_parity/golden_checks.yaml`.

## Plan without training

Planning resolves and audits the immutable input but does not train LightGBM or run a backtest.

macOS/Linux:

```bash
RepoPython=.venv/bin/python
$RepoPython -m qlib_platform.research.workflow.official_parity plan \
  --config configs/pipeline.standalone.yaml \
  --dataset-ref <IMMUTABLE_DATASET_VERSION_OR_REF> \
  --golden-checks artifacts/official_parity/golden_checks.yaml \
  --output-dir artifacts/official_parity/qlib_official_alpha158_lgb_v1
```

Windows PowerShell:

```powershell
$RepoPython = '.\.venv\Scripts\python.exe'
& $RepoPython -m qlib_platform.research.workflow.official_parity plan `
  --config configs/pipeline.standalone.yaml `
  --dataset-ref <IMMUTABLE_DATASET_VERSION_OR_REF> `
  --golden-checks artifacts/official_parity/golden_checks.yaml `
  --output-dir artifacts/official_parity/qlib_official_alpha158_lgb_v1
```

`plan.json` records the scientific identity, immutable DatasetVersion/DataRelease identity, workflow/profile hashes, preregistered tolerances, data semantic audit and golden-check status. Output-directory changes do not alter the scientific identity.

## Run native + platform parity and vendor benchmark

Use the same arguments with `run`:

```bash
RepoPython=.venv/bin/python
$RepoPython -m qlib_platform.research.workflow.official_parity run \
  --config configs/pipeline.standalone.yaml \
  --dataset-ref <IMMUTABLE_DATASET_VERSION_OR_REF> \
  --golden-checks artifacts/official_parity/golden_checks.yaml \
  --output-dir artifacts/official_parity/qlib_official_alpha158_lgb_v1
```

The installed console alias is `tq-official-parity`, but repository development and validation should continue to invoke the repository-local Python module as shown above.

The run executes one native-control run for the first registered seed and qlib-platform compatibility runs for every registered seed. The current profile uses two process-level seeds (`0`, `1`) while preserving the upstream LightGBM kwargs byte-for-byte; this follows the upstream benchmark practice of repeated runs without injecting a model `seed` into the frozen YAML. More repetitions can be introduced only by versioning the profile before observing the corresponding result.

## Outputs

The output directory contains:

- `plan.json` — immutable scientific identity and preflight evidence;
- `runtime_workflow.yaml` — frozen workflow with only the resolved provider URI substituted;
- `native/seed_*/mlruns/` — native Qlib control recorder;
- `platform/seed_*/mlruns/` — compatibility-lane recorders;
- `parity_report.json` — machine-readable engine/data-vendor result;
- `parity_report.md` — human-readable metric table and governance statement.

`parity_report.json` records environment/package versions, git identity, exact workflow hashes, DatasetVersion/DataRelease lineage, semantic and golden checks, per-seed metrics and artifact hashes, engine deltas, official references, local means/stds, preregistered tolerance, PASS/FAIL and attribution.

A non-passing run exits with status code `2`. That is a research result, not an instruction to modify the profile. Investigate the reported attribution, fix actual data/semantic defects when evidence supports doing so, create a new frozen DatasetVersion if necessary, and rerun the same profile.

## Interpretation boundary

A `PASS` means only: **this frozen Tushare-backed DatasetVersion reached the preregistered parity standard under the frozen official Alpha158/LightGBM protocol**. It does not mean the enhanced PIT research is accepted, it does not open the final holdout, and it does not authorize production selection, artifact promotion, broker execution, or live trading.
