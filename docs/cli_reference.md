---
status: ACTIVE
owner: operations
applies_to_commit: 8692afefe1f6cc82ab1f276fca788888f9f30f3e
last_verified: 2026-08-28
---

# CLI Reference

Invoke commands as `<repo-python> -m tushare_qlib [--config PROFILE] COMMAND`. The default profile is
`configs/pipeline.standalone.yaml`.

## Read-only or validation-first commands

| Area | Commands |
| --- | --- |
| Runtime | `status`, `health live`, `health ready`, `health dependencies`, `runtime-probe` |
| Release | `release list`, `release verify` |
| Dataset | `dataset-list`, `dataset-show`, `dataset-resolve`, `dataset-verify` |
| Model | `model-status` |
| Operations | `ops-query`, `ops-summary` |
| Validation | `validate-qrun-contract`, `project-audit`, `research-audit` |
| Phase 3 read-only | `phase3-portable-verify` |

Some validation commands write an explicitly named report. Treat their output path as the authorized
write target.

## Research artifact commands

`feature-store`, `train-select`, `research-run`, `backtest-predictions`, `research-report`,
`alpha-diagnose`, `regime-diagnose`, `attribution-diagnose`, `explanation-diagnose`,
`build-target-portfolio`, `research-gate`, `artifact-v2-export` and `lean-register` create or
register research evidence. Confirm immutable inputs and outputs first.

Production feedback commands create immutable monitoring evidence:

- `feedback-build-labels` validates label maturity against a pinned trading calendar and writes a
  `REALIZED_LABEL_SNAPSHOT`;
- `feedback-evaluate` verifies both parent snapshots, requires complete key coverage and writes a
  `PREDICTION_EVALUATION_SNAPSHOT` with IC/RankIC/spread metrics.

They do not select, promote, deploy or publish models. Both commands require explicit output paths.

## Governed phase commands

Phase 1 and Phase 2 commands remain for historical verification/replay. Phase 3-D exposes only:

- `phase3-validate`;
- `phase3-plan`;
- `phase3-diagnose`;
- `phase3-portable-export`;
- `phase3-portable-verify`.

`phase3-diagnose` and `phase3-portable-export` are state-changing. Phase 3-D does not expose a
confirmation, candidate, selection, holdout-open or publishing command.

## Operational state-changing commands

- ingestion/build: `backfill`, `backfill-extended`, `sync-*`, `daily-sync`, `curate*`,
  `stage-*`, `dump-*`, `dataset-build`, `migrate-qlib-layout --apply`, `registry-rebuild`;
- release/aliases: `release import-qlib`, `release build-local`, `release build-tushare`,
  `release promote`, `dataset-promote`;
- auth/bootstrap: `bootstrap`, `auth bootstrap-admin`, `auth user-create`;
- model/inference: `model-refit`, `model-deploy`, `model-rollback`, `live-inference`,
  `daily-signal-run`;
- delivery: `outbox drain`, `outbox worker`, `ops-retry-delivery`, `ops-ack`.
- feedback artifacts: `feedback-build-labels`, `feedback-evaluate`.

These commands require explicit authorization of date windows, references, deployment IDs, endpoints and
outputs as applicable.
