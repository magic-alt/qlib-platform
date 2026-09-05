---
status: ACTIVE
owner: architecture
applies_to_commit: 8037585f727dd0d1358b5c486ab0867655cd5d90
last_verified: 2026-09-05
---

# Qlib Native Compatibility

`qlib-platform` treats Microsoft Qlib as its native research substrate. Platform governance is additive: it must not turn Qlib's open class/configuration model into a platform allowlist.

## Two execution lanes

The native lane preserves upstream Qlib semantics:

```bash
.venv/bin/tq-qlib run configs/workflow_lightgbm.yaml
```

On Windows use `.venv\\Scripts\\tq-qlib.exe`. `tq-qlib run` delegates directly to `qlib.cli.run.workflow`; it does not rewrite `qlib_init`, `task`, model, dataset, handler, processor, strategy, executor, record templates, or experiment-manager configuration.

The certified platform lane remains separate:

```bash
.venv/bin/python -m qlib_platform --config configs/pipeline.standalone.yaml research-run
```

That lane intentionally adds DatasetVersion pinning, PIT validation, fitted-state isolation, research gates, institutional artifacts, portfolio controls, and promotion rules. Its curated registries are certification surfaces, not declarations of everything Qlib can run.

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

The same mechanism is exposed for Dataset, DataHandler, Processor, Strategy, and Executor objects. A user-defined importable class does not need to be registered in `qlib-platform` first.

## Capability contract

The pinned compatibility manifest is packaged at `qlib_platform/qlib_compat/manifests/qlib-0.9.7.yaml`. It records native core and optional Qlib capability surfaces against the exact supported upstream version.

```bash
.venv/bin/tq-qlib capability-check
.venv/bin/tq-qlib capability-check --require-extra pytorch --require-extra xgboost
```

A future Qlib version upgrade must update this manifest and pass the dedicated capability CI before the platform version constraint is changed.

## Recorder federation

Qlib remains authoritative for its native Recorder and artifacts. `federate_qlib_recorder` indexes the upstream experiment ID, recorder ID, status, tracking URI, artifact URI, parameters, tags, and metrics into `ExperimentStore`. Federation stores references and metadata only; it does not fabricate immutable platform hashes for mutable Qlib artifacts.

## Packaging

`pyqlib==0.9.7` is a core dependency because Qlib is the substrate rather than an optional backend. Heavy families remain explicit through `qlib-full`, `qlib-rl`, `qlib-analysis`, and `qlib-tuner` extras. Existing focused `pytorch`, `xgboost`, `postgres`, `parallel-ray`, and `parallel-dask` extras remain available.

## Non-goals

P4 does not relax Phase 3-D governance, access the sealed final holdout, authorize publishing, or move OMS/broker responsibilities into this repository. It also does not monkey-patch Qlib or copy the upstream `qrun` implementation.
