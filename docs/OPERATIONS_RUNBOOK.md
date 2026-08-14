# Research Inference Operations Runbook

This runbook operates Qlib research inference only. `qlib-platform` does not
read broker state, perform hard-risk checks, create order intents, simulate
fills, reconcile positions, or maintain a trading ledger.

All commands run from the repository root through `./.venv/python.exe` on
Windows or `.venv/bin/python` on macOS/Linux.

## Daily research inference

1. Resolve and verify an immutable platform DataRelease.
2. Run `live-inference` or `daily-signal-run` for the signal date.
3. Review signal health and the research gate.
4. Export Artifact Contract v2 and register it through the platform Qlib
   import boundary.

```powershell
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml dataset-verify <DATA_RELEASE_ID>
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml live-inference `
  --as-of <YYYY-MM-DD> --dataset-ref <DATA_RELEASE_ID>
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml artifact-v2-export `
  <RESEARCH_MANIFEST> --output-dir <EXPORT_DIR> `
  --git-commit <GIT_COMMIT> --container-digest <CONTAINER_DIGEST>
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml lean-register `
  <EXPORT_DIR>/qlib_research_bundle.v2.json
```

Never treat local `model-deploy`, `model-status`, or a Qlib backtest as a
platform Production approval. The maximum state owned here is
`RESEARCH_PROMOTED`.

## Failure handling

- DataRelease, manifest, checksum, lineage, signal-date, or trade-date mismatch:
  fail closed and republish no artifact.
- Signal health or research gate rejection: retain the evidence as rejected;
  do not register a promoted target.
- Import failure: retry the same immutable bundle. Do not mutate an existing
  `externalRunId` or payload.
- Model rollback: select a previously verified local model release for a new
  inference run. Platform deployment state is unaffected.

Broker/QMT operation, hard risk, LEAN validation, Paper, OMS, reconciliation,
ledger recovery, kill switch and Production rollback are documented and run in
the sibling `platform` repository.
