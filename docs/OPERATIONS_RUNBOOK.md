# Production Signal Operations Runbook

This runbook operates the shadow-signal service only. It never submits broker orders.

## Security rules

- Configure `TUSHARE_TOKEN`, `QLIB_REPO`, `QLIB_DATA_URI`, `FEISHU_WEBHOOK_URL`, and optionally
  `FEISHU_WEBHOOK_SECRET` outside the repository.
- Never paste, print, screenshot, or attach the values of those variables to logs, tickets, reports, or
  chat sessions. Diagnostics may mention variable names only.
- Treat model bundles and SQLite state as controlled artifacts. Do not load bundles from arbitrary paths.

## One-time setup

1. Install the project and run `tq --config configs/pipeline.yaml validate-qrun-contract`.
2. Run `tq project-audit --root . --output docs/project_audit.json`.
3. Confirm `data/state/daily_sync/latest.json` is healthy and `pending_publish.json` is clear.
4. Complete a promoted walk-forward research run.

CI enforces the measured repository baseline at 60% total coverage. Treat this as a ratchet: raise the
threshold as legacy integration paths gain tests, and never lower it.
5. Create a production candidate:

   ```powershell
   tq --config configs/pipeline.yaml model-refit --research-run <RESEARCH_RUN_ID> --as-of <YYYY-MM-DD>
   ```

6. Review its manifest/checksums and activate it explicitly:

   ```powershell
   tq --config configs/pipeline.yaml model-deploy <DEPLOYMENT_ID> --device cpu
   tq --config configs/pipeline.yaml model-status
   ```

## Scheduled jobs

### Weekly candidate refit

Schedule on the first trading day of each week after data publication. The command only produces a
`STAGED` bundle; it never changes the deployed model. Human review and `model-deploy` remain mandatory.

### Daily close phase

Run after the configured TuShare readiness time:

```powershell
tq --config configs/pipeline.yaml production-run --phase close --business-date <YYYY-MM-DD>
```

Expected sequence:

```text
daily-sync -> data gate -> deployed bundle verification -> T inference
           -> signal health -> MODEL_SCORE/MODEL_TOPK -> Feishu preview
```

Every trading day must end in an explicit PASS, REJECTED, or FAILED pipeline run. A missing message is not
interpreted as “no trade”; inspect `data/state/ops.sqlite3` and the Task Scheduler result.

### T+1 pretrade phase

Before running, atomically provide:

```text
data/inbox/pretrade/<trade_date>/
  positions.csv
  quotes.csv
  account.json
  fills.csv              # optional after initial reconciliation
  initial_holdings.csv   # required for unexplained starting holdings
```

`positions.csv` and `quotes.csv` must contain one current `as_of_trade_date` and `snapshot_at_utc`.
`account.json` must contain `as_of_trade_date`, `snapshot_at_utc`, `cash`, and `daily_pnl_pct`.

Run:

```powershell
tq --config configs/pipeline.yaml production-run --phase pretrade --business-date <YYYY-MM-DD>
```

The output is an advisory `STRATEGY_DECISION` and `ORDER_INTENT`. Review BUY/SELL/HOLD/BLOCKED manually;
no broker submit API is called.

## Replay and shadow acceptance

```powershell
tq --config configs/pipeline.yaml production-replay `
  --start <YYYY-MM-DD> --end <YYYY-MM-DD> `
  --snapshot-root <FROZEN_DATASET_ROOT>
```

`FROZEN_DATASET_ROOT` must contain one Qlib dataset per date, named `YYYY-MM-DD` or `YYYYMMDD`, and each
`dataset_manifest.json` must end exactly on that signal date. Replay uses an isolated SQLite state and never
writes deployment, signal, or delivery rows into production state. It never sends notifications. Before
considering paper-broker integration, complete at least 20
consecutive trading days with zero duplicate normal messages, zero silent failures, and zero stale signal
execution. Record expected/generated/notified/manual/reconciled outcomes daily.

For research/live parity on one archived date:

```powershell
tq --config configs/pipeline.yaml live-inference `
  --as-of <YYYY-MM-DD> `
  --dataset-uri <FROZEN_QLIB_DATASET> `
  --deployment-id <DEPLOYMENT_ID> `
  --compare-research <OOS_PREDICTIONS> `
  --parity-output <REPORT_JSON>
```

The command exits with code 3 if score or TopK parity fails.

## Incident response

- `MODEL_NOT_DEPLOYED` or `MODEL_STALE`: inspect `model-status`; validate a STAGED bundle and explicitly
  deploy it. Never bypass the age gate.
- `DATASET_LAST_DATE_MISMATCH`, `DAILY_SYNC_NOT_READY`, or `PENDING_PUBLISH`: repair/re-run `daily-sync`.
  Do not publish yesterday's score.
- `CROSS_SECTION_TOO_SMALL` or `DEGENERATE_SCORE`: keep the signal rejected; inspect PIT universe,
  feature schema, and dataset revision state.
- Bundle/manifest/checksum mismatch: quarantine the directory, restore a verified bundle, and use
  `model-rollback --to <DEPLOYMENT_ID>` if appropriate.
- Stale position/quote/account snapshot: refresh the dated inbox. Never relax freshness thresholds during
  an incident.
- Feishu delivery failure: the runner records `FAILED` and exits nonzero. Check Task Scheduler and the
  delivery ledger; after the channel recovers, rerun the same date. SENT messages remain idempotently
  suppressed.

## Rollback

```powershell
tq --config configs/pipeline.yaml model-rollback --to <RETIRED_DEPLOYMENT_ID> --device cpu
```

Rollback validates bundle checksums and the parity fixture before the registry transaction. A signal already
generated for T remains pinned to its original deployment for the T+1 pretrade phase.
