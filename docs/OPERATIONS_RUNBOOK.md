# Production Signal Operations Runbook

This runbook operates the shadow-signal service only. It never submits broker orders.

All commands below run from the repository root. Set `$RepoPython = '.\.venv\python.exe'` once and use the repository-local interpreter. Production and research resolve `research-current` to an immutable Qlib version; verify the resolved version before an operational run.

## Security rules

- Configure `TUSHARE_TOKEN`, `QLIB_REPO`, `QLIB_DATA_URI`, `FEISHU_WEBHOOK_URL`, and optionally
  `FEISHU_WEBHOOK_SECRET` outside the repository.
- Never paste, print, screenshot, or attach the values of those variables to logs, tickets, reports, or
  chat sessions. Diagnostics may mention variable names only.
- Treat model bundles and SQLite state as controlled artifacts. Do not load bundles from arbitrary paths.

## One-time setup

1. Install the project and run `& $RepoPython -m tushare_qlib --config configs/pipeline.yaml validate-qrun-contract`.
2. Run `& $RepoPython -m tushare_qlib --config configs/pipeline.yaml project-audit --root . --output docs/project_audit.json`.
3. Confirm `data/state/daily_sync/latest.json` is healthy and `pending_publish.json` is clear.
4. Complete a promoted walk-forward research run.

CI enforces the measured repository baseline at 60% total coverage. Treat this as a ratchet: raise the
threshold as legacy integration paths gain tests, and never lower it.
5. Create a production candidate:

   ```powershell
   & $RepoPython -m tushare_qlib --config configs/pipeline.yaml model-refit --research-run <RESEARCH_RUN_ID> --as-of <YYYY-MM-DD>
   ```

6. Review its manifest/checksums and activate it explicitly:

   ```powershell
   & $RepoPython -m tushare_qlib --config configs/pipeline.yaml model-deploy <DEPLOYMENT_ID> --device cpu
   & $RepoPython -m tushare_qlib --config configs/pipeline.yaml model-status
   ```

## Scheduled jobs

### Weekly candidate refit

Schedule on the first trading day of each week after data publication. The command only produces a
`STAGED` bundle; it never changes the deployed model. Human review and `model-deploy` remain mandatory.

### Daily close phase

Run after the configured TuShare readiness time:

```powershell
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml production-run --phase close --business-date <YYYY-MM-DD>
```

Expected sequence:

```text
daily-sync -> data gate -> deployed bundle verification -> T inference
           -> signal health -> MODEL_SCORE/MODEL_TOPK -> Feishu preview
```

Every trading day must end in an explicit PASS, REJECTED, or FAILED pipeline run. A missing message is not
interpreted as “no trade”; inspect `data/state/ops.sqlite3` and the Task Scheduler result.

### T+1 pretrade phase

The default `production.broker.kind` and `production.market.kind` are `inbox` for drills. For unattended
account-aware operation, set both to `http_readonly` and point them at user-operated GET-only gateways.
The broker gateway must expose `account`, `positions`, `orders`, and `fills`; the market gateway must
expose fresh quotes with suspension/limit flags and ADV20. The code has no submit/cancel API.

For inbox drills, atomically provide:

```text
data/inbox/pretrade/<trade_date>/
  positions.csv
  quotes.csv
  account.json
  fills.csv              # optional after initial reconciliation
  initial_holdings.csv   # required for unexplained starting holdings
```

`positions.csv` and `quotes.csv` must contain one current `as_of_trade_date` and `snapshot_at_utc`.
`account.json` must contain `as_of_trade_date`, `snapshot_at_utc`, `portfolio_value`, `cash`, and
`daily_pnl_pct`.
The runner stores a read-only copy of every provider response under the signal's
`pretrade/input_snapshot/` directory for incident reconstruction.

Run:

```powershell
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml production-run --phase pretrade --business-date <YYYY-MM-DD>
```

The output is an advisory `STRATEGY_DECISION` and `ORDER_INTENT`. Review BUY/SELL/HOLD/BLOCKED manually;
no broker submit API is called.

## Ops visibility and recovery

Query runs and deliveries without opening SQLite manually:

```powershell
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml ops-query --entity runs --business-date <YYYY-MM-DD>
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml ops-query --entity deliveries --business-date <YYYY-MM-DD>
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml ops-summary --business-date <YYYY-MM-DD> --output <DAILY_JSON>
```

An expired `PENDING` or `FAILED` delivery can be released for a runner retry. This command does not send a
message by itself and cannot reopen a `SENT` delivery:

```powershell
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml ops-retry-delivery <IDEMPOTENCY_KEY>
```

After investigating a failed run or delivery, record an operator and reason:

```powershell
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml ops-ack --entity run --id <RUN_ID> --operator <NAME> --reason <TEXT>
```

## Replay and shadow acceptance

```powershell
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml production-replay `
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
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml live-inference `
  --as-of <YYYY-MM-DD> `
  --dataset-uri <FROZEN_QLIB_DATASET> `
  --deployment-id <DEPLOYMENT_ID> `
  --compare-research <OOS_PREDICTIONS> `
  --parity-output <REPORT_JSON>
```

The command exits with code 3 if score or TopK parity fails.

Run one account-aware shadow day:

```powershell
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml shadow-run `
  --trade-date <YYYY-MM-DD> --shadow-config configs/shadow.yaml
```

The command creates deterministic `INTENT_CREATED -> SIM_ACCEPTED -> SIM_FILLED` events and cumulative
statistics under `data/output/shadow/`. Confirm every daily `metrics.json` contains
`brokerSubmitEnabled: false`. A shadow run never changes broker cash/positions and never calls a broker
write endpoint.

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
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml model-rollback --to <RETIRED_DEPLOYMENT_ID> --device cpu
```

Rollback validates bundle checksums and the parity fixture before the registry transaction. A signal already
generated for T remains pinned to its original deployment for the T+1 pretrade phase.

## Drill checklist

Run these drills in a non-production workspace before enabling unattended shadow scheduling:

- Deploy and restart: activate a verified bundle, restart the process, and confirm `model-status` resolves
  the same deployment and bundle checksum.
- Rollback: deploy a second candidate, rollback to the retired deployment, and confirm one DEPLOYED row.
- Delivery recovery: inject a notifier timeout, verify `FAILED`, run `ops-retry-delivery`, rerun the same
  phase, and confirm exactly one `SENT` row.
- Data recovery: create a non-clear `pending_publish.json`, confirm close fails without publishing a signal,
  repair/re-run `daily-sync`, then confirm the same date can pass.
- Provider failure: inject stale/partial broker and quote responses plus a transient disconnect; confirm
  stale/partial snapshots fail closed and the disconnect retry does not duplicate a run or message.
- Incident acknowledgement: export `ops-summary`, acknowledge the failed entity with operator/reason, and
  retain both JSON summary and input snapshot with the incident record.
- Shadow acceptance: run at least 20 consecutive open days; require zero duplicate signals, stale
  snapshots, silent failures, and broker write calls before any P2 evaluation.
