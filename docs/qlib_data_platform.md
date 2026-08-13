# Qlib Data Platform v3

This repository treats Parquet datasets as the canonical research facts and Qlib Bin as an immutable,
derived representation. LEAN, broker execution, OMS, and order risk are outside this data-platform
contract.

## Layout and lifecycle

```text
data/
├── bronze/tushare/current/       # replaceable materialized view used by normalization
├── bronze/tushare/revisions/     # immutable content-addressed source partitions
├── bronze/versions/              # immutable ingestion snapshots
├── silver/daily/current/         # normalized raw-price facts
├── silver/reference/current/     # calendars and security master
├── silver/versions/              # immutable normalized snapshots
├── gold/pit/current/             # point-in-time financial facts
├── gold/qlib_staging/            # converter working sets
├── gold/versions/                # immutable model-input snapshots
├── qlib/versions/<version_id>/   # immutable Qlib datasets
└── registry/qlib.sqlite          # rebuildable registry index
```

`current` directories are working views, not auditable versions. Each successful dataset publication
freezes Bronze, Silver, and Gold snapshots and records their parent relationships before publishing a
Qlib version. Research resolves `research-current` once at process entry and thereafter uses the resolved
immutable path.

The v3 manifest derives `version_id` from sorted file checksums, the semantic contract, and declared
parents. Build time and filesystem location are excluded. Every file is checksummed. Registry aliases may
only target published versions, and `registry-rebuild` reconstructs aliases and lineage from manifests.

Metadata working views (including the stock master and trading calendar) are atomically replaced. This preserves
the inode of any hard-linked immutable snapshot, so a later metadata refresh cannot mutate a published version.

## PIT and adjustment contracts

- Canonical market data retains raw OHLCV and `adj_factor`; latest-qfq is only an export view.
- Qlib price uses the stable total-return factor anchored to the first valid observation.
- Financial observations without a trustworthy publication timestamp become visible on the first open
  session strictly after `ann_date`/`f_ann_date`.
- Restatements supersede the affected report from their own effective session. A restatement of an older
  period does not replace a later known report period.
- Extended financial ingestion automatically materializes the PIT Gold table.

## Migration

Migration is state-changing and is never implicit. First inspect the plan:

```powershell
$RepoPython = '.\.venv\python.exe'
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml migrate-qlib-layout
```

The dry run reports source/target paths, file counts, bytes, and free space without creating the new
layout. After reviewing it, explicitly authorize and run:

```powershell
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml migrate-qlib-layout --apply
```

The command journals every step under `data/.migration/` and preserves every legacy source directory in
its original location. It builds each new working view in a temporary sibling directory, verifies the
complete file set, sizes, and copied-file checksums, and then atomically publishes the target. Hard links
are used where safe; copied files are byte-verified. Re-running the same `--migration-id` resumes or
returns a completed journal. Legacy Qlib datasets are copied into immutable versions as `QUARANTINED`;
their original directories remain intact and they cannot become `research-current` until rebuilt and
validated under v3.

Migration never deletes legacy data, its original directories, or any pre-existing `.legacy` archive.
Any later cleanup is a separate, explicitly authorized operation.

## Build and registry commands

After Bronze data and extended financial data exist:

```powershell
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml dataset-build `
  --start 20160104 --end 20260810 --single-thread
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml dataset-list
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml dataset-resolve research-current
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml dataset-verify research-current
```

`dataset-build` runs PIT materialization, Silver normalization, Gold export, Qlib conversion, smoke tests,
immutable publication, and alias promotion. A failed build leaves the old alias unchanged.

Use an explicit dataset for reproducible research:

```powershell
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml research-run `
  --mode walk-forward --dataset-ref <VERSION_ID>
```

Other registry operations:

```text
dataset-show <alias-or-version>
dataset-promote <version> --alias research-current
dataset-verify <alias-or-version> [--metadata-only]
registry-rebuild [--root <data-root>]
```

The legacy `curate`, `stage-*`, and `dump-*` commands remain low-level recovery tools. Normal full builds
must use `dataset-build` so PIT generation and Bronze/Silver/Gold lineage cannot be skipped.
