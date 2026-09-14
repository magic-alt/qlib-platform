from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from qlib_platform.data.semantic_bridge import SemanticIngestionClient
from qlib_platform.data.semantic_ingestion import (
    consume_semantic_partition,
    legacy_projection,
    semantic_request_for_day,
)
from qlib_platform.data.sources.base import FetchResult
from qlib_platform.data.sources.semantic import (
    CanonicalBatch,
    DataSourceContractError,
    DatasetCapability,
    DatasetRequest,
    FetchEnvelope,
    PaginationCursor,
    PaginationEvidence,
    PaginationPolicy,
    SourceCapabilities,
    request_fingerprint,
)
from qlib_platform.data.store import PartitionStore


class _ScriptedSemanticSource:
    def __init__(self, envelopes: list[FetchEnvelope]) -> None:
        self.envelopes = list(envelopes)
        self.cursors: list[PaginationCursor | None] = []

    @property
    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities("fixture", (DatasetCapability("equity_daily"),))

    def fetch_dataset(self, request: DatasetRequest) -> FetchEnvelope:
        del request
        if not self.envelopes:
            raise AssertionError("no scripted semantic envelope left")
        return self.envelopes.pop(0)

    def fetch_dataset_paginated(
        self,
        request: DatasetRequest,
        *,
        policy: PaginationPolicy | None = None,
        cursor: PaginationCursor | None = None,
    ) -> FetchEnvelope:
        del request, policy
        self.cursors.append(cursor)
        if not self.envelopes:
            raise AssertionError("no scripted semantic envelope left")
        return self.envelopes.pop(0)


class _Fallback:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def fetch(
        self,
        api_name: str,
        *,
        fields: str | None = None,
        required: bool = True,
        **params: Any,
    ) -> FetchResult:
        self.calls.append((api_name, {"fields": fields, "required": required, **params}))
        return FetchResult(pd.DataFrame({"fallback": [1]}), "success", 1)

    def call(
        self,
        api_name: str,
        *,
        fields: str | None = None,
        required: bool = True,
        **params: Any,
    ) -> pd.DataFrame:
        return self.fetch(api_name, fields=fields, required=required, **params).data


def _daily_batch(*instruments: str) -> CanonicalBatch:
    count = len(instruments)
    frame = pd.DataFrame(
        {
            "instrument": list(instruments),
            "trading_date": ["2026-09-10"] * count,
            "event_time": pd.to_datetime(["2026-09-10T15:00:00+08:00"] * count),
            "available_at": [pd.NaT] * count,
            "open": [10.0 + index for index in range(count)],
            "high": [10.5 + index for index in range(count)],
            "low": [9.5 + index for index in range(count)],
            "close": [10.2 + index for index in range(count)],
            "previous_close": [9.9 + index for index in range(count)],
            "absolute_change": [0.3] * count,
            "return_ratio": [0.03] * count,
            "volume": [1200.0 + 100.0 * index for index in range(count)],
            "turnover": [123400.0 + 1000.0 * index for index in range(count)],
        }
    )
    return CanonicalBatch(
        "equity_daily",
        frame,
        "1.0",
        "Asia/Shanghai",
        source_units={"vol": "hand", "amount": "CNY_thousand"},
        canonical_units={
            "open": "CNY/share",
            "high": "CNY/share",
            "low": "CNY/share",
            "close": "CNY/share",
            "previous_close": "CNY/share",
            "absolute_change": "CNY/share",
            "return_ratio": "ratio",
            "volume": "share",
            "turnover": "CNY",
        },
    )


def _interrupted_and_resumed() -> tuple[DatasetRequest, FetchEnvelope, FetchEnvelope]:
    request = semantic_request_for_day("equity_daily", "20260910")
    fingerprint = request_fingerprint(request)
    cursor = PaginationCursor("fixture", "equity_daily", fingerprint, 2)
    interrupted = FetchEnvelope(
        "fixture",
        "rate_limited",
        3,
        batch=_daily_batch("SH600000", "SZ000001"),
        provider_revision="r1",
        source_hash="a" * 64,
        entitlement="granted",
        error_class="rate_limited",
        error="HTTP 429 rate limit",
        pagination=PaginationEvidence(fingerprint, 0, 2, 1, 2, False, cursor),
    )
    resumed = FetchEnvelope(
        "fixture",
        "incomplete",
        1,
        batch=_daily_batch("SH600001"),
        provider_revision="r1",
        source_hash="b" * 64,
        entitlement="granted",
        error_class="resume_prefix_required",
        error="resumed pagination segment requires the caller's durable prefix before validation",
        pagination=PaginationEvidence(fingerprint, 2, 2, 1, 1, True, None),
    )
    return request, interrupted, resumed


def test_durable_prefix_is_resumed_and_only_complete_sequence_becomes_success(tmp_path) -> None:
    request, interrupted, resumed = _interrupted_and_resumed()
    source = _ScriptedSemanticSource([interrupted, resumed])
    store = PartitionStore(tmp_path / "semantic")
    policy = PaginationPolicy(page_size=2, max_pages=1)

    first = consume_semantic_partition(source, store, request, "20260910", policy=policy)

    assert first.status == "rate_limited"
    assert first.succeeded is False
    assert len(store.read("equity_daily", "20260910")) == 2
    partial_manifest = store.read_manifest("equity_daily", "20260910")
    assert partial_manifest["status"] == "rate_limited"
    assert partial_manifest["pagination"]["next_cursor"]["next_offset"] == 2
    assert partial_manifest["request_fingerprint"] == request_fingerprint(request)

    completed = consume_semantic_partition(source, store, request, "20260910", policy=policy)

    assert completed.status == "success"
    assert completed.batch is not None
    assert completed.batch.data["instrument"].tolist() == ["SH600000", "SZ000001", "SH600001"]
    assert source.cursors[0] is None
    assert source.cursors[1] is not None and source.cursors[1].next_offset == 2
    manifest = store.read_manifest("equity_daily", "20260910")
    assert manifest["status"] == "success"
    assert manifest["attempts_total"] == 4
    assert manifest["page_count_total"] == 2
    assert manifest["pagination"]["terminal"] is True
    assert manifest["pagination"]["next_cursor"] is None


def test_resume_provider_revision_drift_fails_closed(tmp_path) -> None:
    request, interrupted, resumed = _interrupted_and_resumed()
    drifted = FetchEnvelope(
        resumed.provider,
        resumed.status,
        resumed.attempts,
        batch=resumed.batch,
        provider_revision="r2",
        source_hash=resumed.source_hash,
        entitlement=resumed.entitlement,
        error_class=resumed.error_class,
        error=resumed.error,
        pagination=resumed.pagination,
    )
    source = _ScriptedSemanticSource([interrupted, drifted])
    store = PartitionStore(tmp_path / "semantic")

    consume_semantic_partition(source, store, request, "20260910", policy=PaginationPolicy(page_size=2))
    result = consume_semantic_partition(
        source,
        store,
        request,
        "20260910",
        policy=PaginationPolicy(page_size=2),
    )

    assert result.status == "conflict"
    assert result.error_class == "provider_revision_drift"
    assert store.read_manifest("equity_daily", "20260910")["status"] == "conflict"


def test_resume_rejects_request_fingerprint_drift_before_network(tmp_path) -> None:
    request, interrupted, _ = _interrupted_and_resumed()
    source = _ScriptedSemanticSource([interrupted])
    store = PartitionStore(tmp_path / "semantic")
    consume_semantic_partition(source, store, request, "20260910", policy=PaginationPolicy(page_size=2))

    changed = DatasetRequest(
        "equity_daily",
        start="2026-09-10",
        end="2026-09-10",
        fields=("close",),
    )
    with pytest.raises(DataSourceContractError, match="different request fingerprint"):
        consume_semantic_partition(source, store, changed, "20260910", policy=PaginationPolicy(page_size=2))
    assert len(source.cursors) == 1


def test_canonical_daily_projection_preserves_frozen_raw_units_and_fields() -> None:
    projected = legacy_projection(_daily_batch("SH600000"))

    assert projected.columns.tolist() == [
        "ts_code",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "change",
        "pct_chg",
        "vol",
        "amount",
    ]
    row = projected.iloc[0]
    assert row["ts_code"] == "600000.SH"
    assert row["trade_date"] == "20260910"
    assert row["vol"] == pytest.approx(12.0)
    assert row["amount"] == pytest.approx(123.4)
    assert row["pct_chg"] == pytest.approx(3.0)


def test_semantic_bridge_owns_required_daily_but_delegates_optional_endpoints(tmp_path) -> None:
    request = semantic_request_for_day("equity_daily", "20260910")
    fingerprint = request_fingerprint(request)
    semantic = _ScriptedSemanticSource(
        [
            FetchEnvelope(
                "fixture",
                "success",
                1,
                batch=_daily_batch("SH600000"),
                provider_revision="r1",
                source_hash="c" * 64,
                entitlement="granted",
                pagination=PaginationEvidence(fingerprint, 0, 1000, 1, 1, True, None),
            )
        ]
    )
    fallback = _Fallback()
    bridge = SemanticIngestionClient(
        fallback,
        semantic,
        canonical_root=tmp_path / "raw" / "_semantic_v1",
    )
    daily_fields = "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"

    daily = bridge.fetch("daily", fields=daily_fields, required=True, trade_date="20260910")
    optional = bridge.fetch("moneyflow", required=False, trade_date="20260910")

    assert daily.succeeded is True
    assert daily.data["ts_code"].tolist() == ["600000.SH"]
    assert fallback.calls == [
        ("moneyflow", {"fields": None, "required": False, "trade_date": "20260910"})
    ]
    assert optional.data["fallback"].tolist() == [1]
    canonical_manifest = bridge.canonical_store.read_manifest("equity_daily", "20260910")
    assert canonical_manifest["status"] == "success"
    assert canonical_manifest["semantic_contract"] == "canonical_batch_v1"


def test_required_semantic_incomplete_never_reaches_legacy_raw_projection(tmp_path) -> None:
    request, interrupted, _ = _interrupted_and_resumed()
    semantic = _ScriptedSemanticSource([interrupted])
    bridge = SemanticIngestionClient(
        _Fallback(),
        semantic,
        canonical_root=tmp_path / "raw" / "_semantic_v1",
        pagination_policy=PaginationPolicy(page_size=2, max_pages=1),
    )

    with pytest.raises(DataSourceContractError, match="rate_limited"):
        bridge.fetch("daily", required=True, trade_date="20260910")
    assert bridge.canonical_store.read_manifest("equity_daily", "20260910")["status"] == "rate_limited"
