---
status: ACTIVE
owner: architecture
applies_to_commit: f8eba3e5a54b0e9571e2bcdde492c08ef1140091
last_verified: 2026-09-16
---

# Qlib Native Compatibility

`qlib-platform` treats Microsoft Qlib as its native research substrate. Platform governance is additive: it must not turn Qlib's open class/configuration model into a platform allowlist, and it must not describe an importable upstream surface as behaviorally certified unless named conformance evidence exists.

## Compatibility terminology

The compatibility manifest uses four terms deliberately:

| Term | Meaning |
| --- | --- |
| **Upstream-native / pass-through** | The platform delegates to the upstream Qlib implementation without rewriting its semantics. Importability is checked, but no behavioral parity claim is implied. |
| **Certified parity** | The capability has named positive conformance evidence; negative evidence is required where the wrapper could otherwise hide or rewrite an upstream failure. |
| **Platform extension** | Functionality implemented under `qlib_platform.*` on top of Qlib, such as immutable releases, PIT-aware research inputs, governed workflows and artifact lineage. |
| **Unsupported / not certified** | The upstream surface either has no compatibility commitment or is explicitly excluded by a documented deviation. |

`qlib-platform` does **not** currently claim full upstream compatibility for every Qlib 0.9.7 public surface. The machine-readable matrix must reach its explicit 100% certification gate before that wording is allowed.

## Two execution lanes

The native lane preserves upstream Qlib semantics:

```bash
.venv/bin/tq-qlib run configs/workflow_lightgbm.yaml
```

On Windows use `.venv\\Scripts\\tq-qlib.exe`. `tq-qlib run` delegates directly to `qlib.cli.run.workflow`; it does not rewrite `qlib_init`, `task`, model, dataset, handler, processor, strategy, executor, record templates, or experiment-manager configuration.

The governed platform lane remains separate:

```bash
.venv/bin/python -m qlib_platform --config configs/pipeline.standalone.yaml research-run
```

That lane intentionally adds DatasetVersion pinning, PIT validation, fitted-state isolation, research gates, institutional artifacts, portfolio controls, and promotion rules. Its curated registries are platform certification surfaces, not declarations of everything Qlib can run.

## Generic Qlib object construction

The compatibility API delegates object construction to Qlib's own `init_instance_by_config`:

```python
from qlib_platform.qlib_compat import QlibObjectSpec, build_model

model = build_model(
    QlibObjectSpec(
        class_name="HIST",
        module_path="qlib.contrib.model.pytorch_hist",
        kwargs={"d_feat": 6, "hidden_size": 128},
    )
)
```

The same mechanism is exposed for Dataset, DataHandler, Processor, Strategy, and Executor objects. A user-defined importable class does not need to be registered in `qlib-platform` first. Import or construction failures are propagated from upstream rather than converted into platform success.

## Machine-readable capability matrix

The pinned compatibility manifest is packaged at `src/qlib_platform/qlib_compat/manifests/qlib-0.9.7.yaml`. Schema v2 materializes, for every declared capability:

- required or optional level;
- optional dependency / installation extra;
- certification status;
- owner and Qlib version;
- OS/Python certification matrix;
- positive and, where required, negative evidence;
- known deviation.

Render the matrix without probing imports:

```bash
.venv/bin/tq-qlib matrix --output artifacts/validation/qlib_compatibility_matrix.json
```

Probe the current environment:

```bash
.venv/bin/tq-qlib capability-check
.venv/bin/tq-qlib capability-check --require-extra pytorch --require-extra xgboost
```

Runtime status is intentionally not a boolean `PASS` for every entry:

| Runtime status | Meaning |
| --- | --- |
| `CERTIFIED` | Target is available and the manifest classifies it as certified. |
| `AVAILABLE` | Target is importable but only upstream-native/pass-through. |
| `NOT_CERTIFIED` | Target exists but the platform makes no compatibility commitment. |
| `UNAVAILABLE` | Optional target/dependency is absent; this is not reported as PASS. |
| `UNSUPPORTED` | Explicitly excluded by a documented compatibility deviation. |
| `FAIL` | A required target, or an explicitly required optional extra, is unavailable. |

The top-level `passed` field is the blocking pinned-environment contract. `certificationSummary.fullCompatibilityClaimEligible` is a separate and stricter claim gate.

## Current certified corpus

The current `certified` entries are limited to surfaces with named evidence, including:

- direct Qlib object construction passthrough;
- native `qrun` delegation and failure propagation;
- `task_train` delegation and failure propagation;
- Recorder federation/native recorder behavior;
- `DatasetH`, `Alpha158`, LightGBM, SignalRecord, SigAnaRecord and PortAnaRecord used by the real conformance workflow;
- `TopkDropoutStrategy` and Exchange behavior exercised by that workflow.

`tests/test_qlib_real_conformance.py` builds a deterministic, real-shaped Qlib binary provider, runs the same Alpha158/LightGBM workflow through upstream `qlib.cli.run.workflow` and `tq-qlib run`, and compares required artifact schema plus prediction/signal/portfolio outputs with numeric tolerances. Synthetic/static import checks remain useful, but they do not upgrade unrelated surfaces to certified parity.

The independent Tushare/official-benchmark lane from Issue #130 answers a different question: whether frozen market data can reproduce the official Alpha158/LightGBM protocol. Market-data benchmark parity must not be used to inflate the upstream API capability matrix.

## Optional dependency semantics

Optional model/analysis modules are capability entries, not implicit promises. If an optional dependency is not installed, the entry is `UNAVAILABLE`. CI promotes selected extras to required with `--require-extra`; only then does absence become a blocking `FAIL`.

This distinction prevents a base installation from claiming that PyTorch/CatBoost/analysis tooling passed merely because those packages were not exercised.

## Upstream drift canary

`.github/workflows/qlib-upstream-canary.yml` runs on a weekly schedule and by manual dispatch against:

1. the latest released `pyqlib` package;
2. Microsoft Qlib `main`.

Each candidate is installed in an isolated virtual environment and probed with `--allow-version-drift`. The report records expected/actual version, required import drift and runtime status counts. The canary is intentionally non-blocking and does not run on ordinary pull requests; upstream transient failures must not break the pinned Qlib 0.9.7 production baseline.

The blocking PR gate remains `.github/workflows/qlib-capability.yml` against the pinned version.

## Qlib upgrade checklist

A Qlib version bump is not complete when `pip install` succeeds. An upgrade PR must, in this order:

1. review the latest upstream release notes and the scheduled canary report;
2. create a new versioned compatibility manifest rather than overwriting historical certification evidence;
3. run a capability diff and classify added/removed/moved surfaces as `certified`, `upstream-native`, `not-certified` or `unsupported`;
4. add or update named conformance evidence before changing any entry to `certified`;
5. rerun native qrun/recorder/artifact parity and all required negative fixtures;
6. update known deviations and optional-dependency policy explicitly; never hide a breaking change behind a compatibility shim;
7. only after the manifest and evidence are green, update the `pyqlib` version pin and dependency constraints;
8. update this document and README claims to match the resulting matrix. If the full-compatibility gate is not satisfied, do not use full-compatibility wording.

Historical manifests and run evidence remain immutable so an old experiment can still be interpreted under the Qlib contract that produced it.

## Recorder federation

Qlib remains authoritative for its native Recorder and artifacts. `federate_qlib_recorder` indexes the upstream experiment ID, recorder ID, status, tracking URI, artifact URI, parameters, tags, and metrics into `ExperimentStore`. Federation stores references and metadata only; it does not fabricate immutable platform hashes for mutable Qlib artifacts.

## Packaging

`pyqlib==0.9.7` is a core dependency because Qlib is the substrate rather than an optional backend. Heavy model/analysis capabilities are explicit through `qlib-full`, `qlib-analysis`, and `qlib-tuner`; focused `pytorch`, `xgboost`, `postgres`, `parallel-ray`, and `parallel-dask` extras remain available.

### Known upstream RL dependency exception

Qlib 0.9.7 declares `tianshou<=0.4.10` for its optional RL order-execution stack. Tianshou 0.4.10 in turn declares `protobuf~=3.19.0`, and the repository's fail-closed dependency audit identifies that protobuf line as vulnerable. Qlib also pinned Tianshou to this legacy range because later Tianshou versions were known to break its RL integration.

The repository therefore does **not** weaken dependency review, silently upgrade Tianshou beyond Qlib's supported range, or distribute the vulnerable legacy chain through a `qlib-rl` extra. `qlib.rl.order_execution` is classified as `unsupported` with a documented deviation. The base `qlib.rl` namespace remains an upstream-native required import, but that must not be read as certification of the excluded order-execution dependency stack.

Resolution requires Microsoft Qlib to certify a newer Tianshou API or a separately reviewed, versioned compatibility change with new evidence.

## Non-goals

This compatibility contract does not relax governed research/holdout rules, authorize model publishing, move OMS/broker responsibilities into this repository, monkey-patch Qlib, or copy the upstream `qrun` implementation. It also does not treat high backtest returns as compatibility evidence.
