# Semantic ingestion storage boundary

The ingestion pipeline keeps the existing raw consumer contract stable while moving ownership of the required daily datasets to the provider-neutral semantic DataSource SDK.

For providers that advertise a semantic source, `daily`, `daily_basic`, and `adj_factor` are fetched as `DatasetRequest` objects and persisted first as canonical batches under `raw/_semantic_v1/<dataset_kind>/trade_date=<YYYYMMDD>/`. Provider paging syntax remains inside the adapter. The canonical manifest records the semantic request fingerprint, provider revision, units, source hash, entitlement, cumulative attempts, page count, and any resume cursor.

A pagination cursor is not sufficient evidence by itself. When a paginated fetch is interrupted, validated rows are retained as a durable canonical prefix together with the cursor. A later attempt must resume the exact request, preserve provider/revision continuity, merge the durable prefix with the resumed tail, and re-run schema, duplicate-key, and coverage validation. Only a terminal, fully valid sequence may become `success`.

After canonical success, a compatibility projection recreates the frozen raw columns and source units currently consumed by curation and normalization. This prevents the semantic migration from silently changing existing DatasetVersion inputs or research results. Historical raw partitions are not rewritten; existing terminal raw partitions continue to short-circuit ingestion unless the caller explicitly requests a forced refresh.

Optional endpoints and providers that do not yet advertise semantic ingestion continue through the existing `DataSourceClient` transport contract. This is a capability boundary rather than a provider-name branch in `Extractor`: a new semantic provider supplies its adapter/binding and can reuse the same ingestion consumer.

The migration is fail-closed. Incomplete pagination, request-fingerprint drift, provider or revision drift, schema mismatch, duplicate keys, permission failures, and other non-success semantic states cannot be projected into the legacy raw store as completed data.
