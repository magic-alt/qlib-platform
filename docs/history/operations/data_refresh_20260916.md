---
status: HISTORICAL
owner: operations
applies_to_commit: 095670e9087748f7d2a5bba7057178fb24764965
last_verified: 2026-09-16
---

# Local market-data refresh — 2026-09-16

This record summarizes an operator-authorized refresh of the local standalone data root. Generated data,
credentials, and local state remain untracked under `data/` and are not part of this repository change.

## Scope and final layout

- The provider-neutral Bronze working view is `data/bronze/market/current/`.
- Base raw `daily`, `adj_factor`, and `daily_basic` partitions were refreshed through `20260915`.
- All 28 configured extended endpoints were present after the refresh.
- Symbol-partitioned extended endpoints use `trade_date=<code>.<exchange>`, for example
  `trade_date=000001.SZ`. The underscore form `trade_date=000001_SZ` was a legacy writer layout.
- `data/bronze/tushare/current/` was removed only after the canonical working view was verified. Historical
  revision and migration-audit material was retained for recovery and provenance.

## Extended refresh result

The final refresh of the `basic`, `corporate_action`, and `holder` groups completed with:

| Result | Count |
| --- | ---: |
| Successful requests | 31,562 |
| Valid empty responses | 3,739 |
| Changed partitions | 10,949 |
| Failed requests | 0 |
| Permission-denied requests | 0 |

The earlier daily catch-up refreshed the seven `market_reference` endpoints for the ten open dates from
`20260902` through `20260915` and refreshed the eight financial endpoints over the configured four-quarter
window.

The final local validation covered all 28 endpoints and 65,744 stored partitions:

| Stored status | Count |
| --- | ---: |
| `success` | 58,167 |
| `empty` | 7,577 |
| Manifest/file/SHA-256 errors | 0 |

## Offline symbol-layout normalization

Normalization ran only after the network refresh completed and made no provider requests.

- Removed 24,972 legacy underscore directories that had a verified canonical dot-format replacement.
- Renamed 40 underscore-only directories in place and updated only their partition metadata.
- Verified that each renamed Parquet file retained its original SHA-256.
- Confirmed zero remaining underscore-format symbol partitions across `namechange`, `dividend`,
  `pledge_stat`, `pledge_detail`, `stk_managers`, and `stk_rewards`.
- Reclaimed 126,329,670 bytes from duplicate symbol partitions.

## Cleanup decisions

Removed items were limited to confirmed duplicates, Finder metadata, an empty obsolete derived-data shell,
an empty migration placeholder, and the verified legacy `bronze/tushare/current` working view.

The following data was retained because it still carries identity, reproducibility, recovery, or provenance
value:

- immutable DataRelease history under `data/releases/`;
- DatasetVersion registry state and the active derived Qlib materialization;
- feature caches and provider-raw object-store content;
- `data/bronze/market/revisions/` and `data/bronze/tushare/revisions/`;
- the storage-layout migration journal and configured empty runtime directories.

## Publication status

The refreshed raw partitions were written through `20260915`, but the daily publish remained fail-closed.
Historical `daily_basic` coverage and older manifests did not satisfy the current quality gate, so the job
left `data/state/daily_sync/pending_publish.json` pending and did not move the active DataRelease or
DatasetVersion aliases. A later recovery run must resolve that gate before publication.

Local evidence was written to:

- `data/state/extended_backfill/last_run.json`
- `data/state/extended_backfill/final_validation_20260916.json`
- `data/state/layout_cleanup/symbol_partition_cleanup_20260916.json`
- `data/state/data_cleanup/cleanup_20260916.json`
