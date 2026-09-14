from __future__ import annotations

import pandas as pd

from qlib_platform.data.semantic_ingestion import consume_semantic_partition
from qlib_platform.data.sources.semantic import (
    CanonicalBatch,
    DatasetCapability,
    DatasetRequest,
    FetchEnvelope,
    SourceCapabilities,
)
from qlib_platform.data.store import PartitionStore


class _NonPaginatedSource:
    def __init__(self, envelope: FetchEnvelope) -> None:
        self.envelope = envelope
        self.calls = 0

    @property
    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities("file_fixture", (DatasetCapability("equity_daily"),))

    def fetch_dataset(self, request: DatasetRequest) -> FetchEnvelope:
        del request
        self.calls += 1
        return self.envelope


def test_non_paginated_semantic_source_is_persisted_as_canonical_success(tmp_path) -> None:
    request = DatasetRequest("equity_daily", start="2026-09-10", end="2026-09-10")
    frame = pd.DataFrame(
        {
            "instrument": ["SH600000"],
            "trading_date": ["2026-09-10"],
            "event_time": pd.to_datetime(["2026-09-10T15:00:00+08:00"]),
            "close": [10.0],
        }
    )
    batch = CanonicalBatch("equity_daily", frame, "1.0", "Asia/Shanghai")
    source = _NonPaginatedSource(
        FetchEnvelope(
            "file_fixture",
            "success",
            1,
            batch=batch,
            provider_revision="fixture-v1",
            source_hash="d" * 64,
            entitlement="granted",
        )
    )
    store = PartitionStore(tmp_path / "semantic")

    result = consume_semantic_partition(source, store, request, "20260910")

    assert result.status == "success"
    assert result.pagination is None
    assert source.calls == 1
    manifest = store.read_manifest("equity_daily", "20260910")
    assert manifest["provider"] == "file_fixture"
    assert manifest["provider_revision"] == "fixture-v1"
    assert manifest["status"] == "success"


def test_non_paginated_semantic_failure_writes_status_without_payload(tmp_path) -> None:
    request = DatasetRequest("equity_daily", start="2026-09-10", end="2026-09-10")
    source = _NonPaginatedSource(
        FetchEnvelope(
            "file_fixture",
            "schema_mismatch",
            1,
            error_class="missing_columns",
            error="missing close",
        )
    )
    store = PartitionStore(tmp_path / "semantic")

    result = consume_semantic_partition(source, store, request, "20260910")

    assert result.status == "schema_mismatch"
    assert store.exists("equity_daily", "20260910") is False
    manifest = store.read_manifest("equity_daily", "20260910")
    assert manifest["status"] == "schema_mismatch"
    assert manifest["error_class"] == "missing_columns"
