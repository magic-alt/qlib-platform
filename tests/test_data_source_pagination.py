from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from qlib_platform.data.sources.base import FetchResult
from qlib_platform.data.sources.semantic import (
    CanonicalBatch,
    DataSourceContractError,
    DatasetRequest,
    FetchEnvelope,
    PaginationCursor,
    PaginationPolicy,
    collect_paginated,
    frame_sha256,
    request_fingerprint,
    require_usable,
)
from qlib_platform.data.sources.tushare_semantic import TushareSemanticDataSource


class _StubClient:
    def __init__(self, results: dict[str, FetchResult | list[FetchResult]]) -> None:
        self.results = results
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
        result = self.results[api_name]
        if isinstance(result, list):
            if not result:
                raise AssertionError(f"no recorded result left for {api_name}")
            return result.pop(0)
        return result

    def call(
        self,
        api_name: str,
        *,
        fields: str | None = None,
        required: bool = True,
        **params: Any,
    ) -> pd.DataFrame:
        return self.fetch(api_name, fields=fields, required=required, **params).data


def _daily_rows(*codes: str) -> pd.DataFrame:
    count = len(codes)
    return pd.DataFrame(
        {
            "ts_code": list(codes),
            "trade_date": ["20260910"] * count,
            "open": [10.0 + i for i in range(count)],
            "high": [10.5 + i for i in range(count)],
            "low": [9.5 + i for i in range(count)],
            "close": [10.2 + i for i in range(count)],
            "pre_close": [9.9 + i for i in range(count)],
            "change": [0.3] * count,
            "pct_chg": [3.0] * count,
            "vol": [10.0 + i for i in range(count)],
            "amount": [100.0 + i for i in range(count)],
        }
    )


def _daily_request(**kwargs: Any) -> DatasetRequest:
    values: dict[str, Any] = {
        "dataset_kind": "equity_daily",
        "start": "2026-09-10",
        "end": "2026-09-10",
    }
    values.update(kwargs)
    return DatasetRequest(**values)


def _canonical_page(
    instruments: list[str],
    *,
    provider: str = "fixture",
    timezone: str = "UTC",
    revision: str | None = "r1",
) -> FetchEnvelope:
    frame = pd.DataFrame(
        {
            "instrument": instruments,
            "event_time": ["2026-09-10T07:00:00Z"] * len(instruments),
            "close": [10.0 + index for index in range(len(instruments))],
        }
    )
    batch = CanonicalBatch("equity_daily", frame, "1.0", timezone)
    return FetchEnvelope(
        provider,
        "success",
        1,
        batch=batch,
        provider_revision=revision,
        source_hash=frame_sha256(frame),
        entitlement="granted",
    )


def test_tushare_pagination_merges_pages_and_keeps_provider_offsets_private():
    client = _StubClient(
        {
            "daily": [
                FetchResult(_daily_rows("600000.SH", "600001.SH"), "success", 1),
                FetchResult(_daily_rows("600002.SH"), "success", 1),
            ]
        }
    )
    source = TushareSemanticDataSource(client)
    request = _daily_request()

    envelope = source.fetch_dataset_paginated(request, policy=PaginationPolicy(page_size=2, max_pages=5))
    batch = require_usable(envelope, request)

    assert batch.data["instrument"].tolist() == ["SH600000", "SH600001", "SH600002"]
    assert envelope.pagination is not None
    assert envelope.pagination.page_count == 2
    assert envelope.pagination.row_count == 3
    assert envelope.pagination.terminal is True
    assert envelope.pagination.next_cursor is None
    assert [call[1]["offset"] for call in client.calls] == [0, 2]
    assert [call[1]["limit"] for call in client.calls] == [2, 2]
    assert "offset" not in request_fingerprint(request)


def test_exact_full_page_uses_explicit_empty_page_as_terminal_evidence():
    client = _StubClient(
        {
            "daily": [
                FetchResult(_daily_rows("600000.SH", "600001.SH"), "success", 1),
                FetchResult(pd.DataFrame(), "empty", 1),
            ]
        }
    )
    source = TushareSemanticDataSource(client)
    request = _daily_request()

    envelope = source.fetch_dataset_paginated(request, policy=PaginationPolicy(page_size=2, max_pages=3))

    assert envelope.status == "success"
    assert envelope.batch is not None and len(envelope.batch.data) == 2
    assert envelope.pagination is not None
    assert envelope.pagination.page_count == 1
    assert envelope.pagination.terminal is True
    assert [call[1]["offset"] for call in client.calls] == [0, 2]


def test_midstream_rate_limit_returns_partial_evidence_and_bound_resume_cursor():
    client = _StubClient(
        {
            "daily": [
                FetchResult(_daily_rows("600000.SH", "600001.SH"), "success", 1),
                FetchResult(pd.DataFrame(), "failed", 3, "HTTP 429 rate limit"),
            ]
        }
    )
    source = TushareSemanticDataSource(client)
    request = _daily_request()

    interrupted = source.fetch_dataset_paginated(request, policy=PaginationPolicy(page_size=2, max_pages=4))

    assert interrupted.status == "rate_limited"
    assert interrupted.succeeded is False
    assert interrupted.batch is not None and len(interrupted.batch.data) == 2
    assert interrupted.pagination is not None
    assert interrupted.pagination.page_count == 1
    assert interrupted.pagination.terminal is False
    assert interrupted.pagination.next_cursor is not None
    assert interrupted.pagination.next_cursor.next_offset == 2
    assert interrupted.pagination.next_cursor.request_fingerprint == request_fingerprint(request)
    with pytest.raises(DataSourceContractError, match="rate_limited"):
        require_usable(interrupted, request)


def test_resume_cursor_starts_at_failed_offset_without_refetching_prior_pages():
    initial_client = _StubClient(
        {
            "daily": [
                FetchResult(_daily_rows("600000.SH", "600001.SH"), "success", 1),
                FetchResult(pd.DataFrame(), "failed", 1, "request timed out"),
            ]
        }
    )
    request = _daily_request()
    initial = TushareSemanticDataSource(initial_client).fetch_dataset_paginated(
        request,
        policy=PaginationPolicy(page_size=2, max_pages=4),
    )
    assert initial.pagination is not None and initial.pagination.next_cursor is not None

    resumed_client = _StubClient({"daily": FetchResult(_daily_rows("600002.SH"), "success", 1)})
    resumed = TushareSemanticDataSource(resumed_client).fetch_dataset_paginated(
        request,
        policy=PaginationPolicy(page_size=2, max_pages=4),
        cursor=initial.pagination.next_cursor,
    )

    assert resumed.status == "incomplete"
    assert resumed.succeeded is False
    assert resumed.error_class == "resume_prefix_required"
    assert resumed.error == "resumed pagination segment requires the caller's durable prefix before validation"
    assert resumed.batch is not None
    assert resumed.batch.data["instrument"].tolist() == ["SH600002"]
    assert resumed.pagination is not None
    assert resumed.pagination.start_offset == 2
    assert resumed_client.calls[0][1]["offset"] == 2
    with pytest.raises(DataSourceContractError, match="incomplete"):
        require_usable(resumed, request)


@pytest.mark.parametrize(
    "cursor",
    [
        PaginationCursor("other", "equity_daily", "x" * 64, 2),
        PaginationCursor("tushare", "other", "x" * 64, 2),
        PaginationCursor("tushare", "equity_daily", "x" * 64, 2),
    ],
)
def test_resume_cursor_rejects_provider_dataset_or_request_drift(cursor: PaginationCursor):
    source = TushareSemanticDataSource(_StubClient({"daily": FetchResult(pd.DataFrame(), "empty", 1)}))

    with pytest.raises(DataSourceContractError, match="pagination cursor"):
        source.fetch_dataset_paginated(_daily_request(), cursor=cursor)


def test_max_page_budget_returns_incomplete_with_next_offset_even_if_coverage_matches():
    client = _StubClient({"daily": FetchResult(_daily_rows("600000.SH", "600001.SH"), "success", 1)})
    source = TushareSemanticDataSource(client)
    request = _daily_request()

    envelope = source.fetch_dataset_paginated(request, policy=PaginationPolicy(page_size=2, max_pages=1))

    assert envelope.status == "incomplete"
    assert envelope.batch is not None and len(envelope.batch.data) == 2
    assert envelope.pagination is not None
    assert envelope.pagination.terminal is False
    assert envelope.pagination.next_cursor is not None
    assert envelope.pagination.next_cursor.next_offset == 2
    assert envelope.error == "pagination limit reached before a terminal page"


def test_cross_page_duplicate_is_a_conflict_not_silent_deduplication():
    client = _StubClient(
        {
            "daily": [
                FetchResult(_daily_rows("600000.SH", "600001.SH"), "success", 1),
                FetchResult(_daily_rows("600001.SH"), "success", 1),
            ]
        }
    )
    source = TushareSemanticDataSource(client)

    envelope = source.fetch_dataset_paginated(
        _daily_request(),
        policy=PaginationPolicy(page_size=2, max_pages=3),
    )

    assert envelope.status == "conflict"
    assert envelope.error_class == "conflict"
    assert envelope.batch is not None and len(envelope.batch.data) == 3


def test_daily_basic_and_adjustment_factor_pagination_stay_inside_provider_adapter():
    basic_raw = pd.DataFrame(
        {
            "ts_code": ["600000.SH"],
            "trade_date": ["20260910"],
            "total_share": [100.0],
            "float_share": [80.0],
            "free_share": [60.0],
            "total_mv": [250.0],
            "circ_mv": [200.0],
        }
    )
    adj_raw = pd.DataFrame({"ts_code": ["600000.SH"], "trade_date": ["20260910"], "adj_factor": [1.2]})
    client = _StubClient(
        {
            "daily_basic": FetchResult(basic_raw, "success", 1),
            "adj_factor": FetchResult(adj_raw, "success", 1),
        }
    )
    source = TushareSemanticDataSource(client)

    basic = source.fetch_dataset_paginated(
        DatasetRequest("equity_daily_basic", start="2026-09-10", end="2026-09-10"),
        policy=PaginationPolicy(page_size=2),
    )
    adjustment = source.fetch_dataset_paginated(
        DatasetRequest("adjustment_factor", start="2026-09-10", end="2026-09-10"),
        policy=PaginationPolicy(page_size=2),
    )

    assert basic.status == "success" and basic.batch is not None
    assert basic.batch.data.loc[0, "total_shares"] == 1_000_000.0
    assert adjustment.status == "success" and adjustment.batch is not None
    assert adjustment.batch.data.loc[0, "adjustment_factor"] == pytest.approx(1.2)
    assert [call[1]["offset"] for call in client.calls] == [0, 0]


def test_paginated_fetch_rejects_dataset_without_paging_contract():
    source = TushareSemanticDataSource(_StubClient({}))

    envelope = source.fetch_dataset_paginated(DatasetRequest("instrument_master", require_complete=False))

    assert envelope.status == "unsupported"
    assert envelope.error_class == "pagination_unsupported"


def test_optional_empty_pagination_is_terminal_empty_while_required_empty_is_incomplete():
    optional_source = TushareSemanticDataSource(
        _StubClient({"daily": FetchResult(pd.DataFrame(), "empty", 1)})
    )
    required_source = TushareSemanticDataSource(
        _StubClient({"daily": FetchResult(pd.DataFrame(), "empty", 1)})
    )

    optional = optional_source.fetch_dataset_paginated(_daily_request(require_complete=False))
    required = required_source.fetch_dataset_paginated(_daily_request(require_complete=True))

    assert optional.status == "empty"
    assert optional.pagination is not None and optional.pagination.terminal is True
    assert required.status == "incomplete"
    assert required.error_class == "required_data_empty"


def test_generic_collector_rejects_page_provider_and_contract_drift():
    request = _daily_request(require_complete=False)
    provider_mismatch = collect_paginated(
        provider="fixture",
        request=request,
        policy=PaginationPolicy(page_size=2, max_pages=2),
        page_fetcher=lambda _offset, _limit: _canonical_page(["SH600000"], provider="other"),
    )
    assert provider_mismatch.status == "conflict"
    assert provider_mismatch.error_class == "provider_mismatch"

    pages = iter(
        [
            _canonical_page(["SH600000", "SH600001"]),
            _canonical_page(["SH600002"], timezone="Asia/Shanghai"),
        ]
    )
    contract_drift = collect_paginated(
        provider="fixture",
        request=request,
        policy=PaginationPolicy(page_size=2, max_pages=2),
        page_fetcher=lambda _offset, _limit: next(pages),
    )
    assert contract_drift.status == "schema_mismatch"
    assert contract_drift.error_class == "page_contract_drift"


def test_generic_collector_rejects_provider_revision_drift():
    request = _daily_request(require_complete=False)
    pages = iter(
        [
            _canonical_page(["SH600000", "SH600001"], revision="r1"),
            _canonical_page(["SH600002"], revision="r2"),
        ]
    )

    envelope = collect_paginated(
        provider="fixture",
        request=request,
        policy=PaginationPolicy(page_size=2, max_pages=2),
        page_fetcher=lambda _offset, _limit: next(pages),
    )

    assert envelope.status == "conflict"
    assert envelope.error_class == "provider_revision_drift"
    assert envelope.pagination is not None and envelope.pagination.next_cursor is not None
    assert envelope.pagination.next_cursor.next_offset == 2


def test_pagination_policy_cursor_and_request_fingerprint_are_fail_closed():
    with pytest.raises(ValueError, match="page_size"):
        PaginationPolicy(page_size=0)
    with pytest.raises(ValueError, match="max_pages"):
        PaginationPolicy(max_pages=0)
    with pytest.raises(ValueError, match="provider"):
        PaginationCursor(" ", "equity_daily", "x", 0)
    with pytest.raises(ValueError, match="dataset_kind"):
        PaginationCursor("fixture", " ", "x", 0)
    with pytest.raises(ValueError, match="next_offset"):
        PaginationCursor("fixture", "equity_daily", "x", -1)

    first = _daily_request(fields=("close",))
    same = _daily_request(fields=("close",))
    different = _daily_request(fields=("open",))
    assert request_fingerprint(first) == request_fingerprint(same)
    assert request_fingerprint(first) != request_fingerprint(different)
