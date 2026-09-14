from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from qlib_platform.data.sources.base import FetchResult
from qlib_platform.data.sources.semantic import (
    CanonicalBatch,
    DataSourceContractError,
    DatasetCapability,
    DatasetRequest,
    FetchEnvelope,
    LocalDatasetSpec,
    LocalFileDataSource,
    RecordedDataSource,
    SourceCapabilities,
    canonical_coverage,
    validate_canonical_batch,
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


def test_request_requires_nonempty_semantic_identifiers():
    with pytest.raises(ValueError, match="dataset_kind"):
        DatasetRequest(" ")
    with pytest.raises(ValueError, match="frequency"):
        DatasetRequest("equity_daily", frequency=" ")
    with pytest.raises(ValueError, match="adjustment"):
        DatasetRequest("equity_daily", adjustment=" ")
    with pytest.raises(ValueError, match="schema_version"):
        DatasetRequest("equity_daily", schema_version=" ")


def test_fetch_envelope_rejects_invalid_state_and_attempt_count():
    with pytest.raises(ValueError, match="unsupported fetch status"):
        FetchEnvelope("fixture", "made_up", 1)
    with pytest.raises(ValueError, match="attempts must be positive"):
        FetchEnvelope("fixture", "success", 0)


def test_canonical_coverage_handles_empty_or_missing_time_column():
    request = DatasetRequest("equity_daily", start="2026-09-10", end="2026-09-11")

    empty = canonical_coverage(pd.DataFrame(), event_time_column="event_time", request=request)
    missing = canonical_coverage(
        pd.DataFrame({"instrument": ["SH600000"]}),
        event_time_column="event_time",
        request=request,
    )

    assert empty.row_count == 0 and empty.complete is False
    assert missing.row_count == 1 and missing.complete is False


def test_canonical_validation_rejects_identity_schema_time_fields_and_incomplete_coverage():
    request = DatasetRequest("equity_daily", fields=("close",), require_complete=True)
    no_time = CanonicalBatch("equity_daily", pd.DataFrame({"close": [10.0]}), "1.0", "UTC")
    wrong_kind = CanonicalBatch(
        "other",
        pd.DataFrame({"event_time": ["2026-09-10T00:00:00Z"], "close": [10.0]}),
        "1.0",
        "UTC",
    )
    wrong_schema = CanonicalBatch(
        "equity_daily",
        pd.DataFrame({"event_time": ["2026-09-10T00:00:00Z"], "close": [10.0]}),
        "2.0",
        "UTC",
    )
    missing_field = CanonicalBatch(
        "equity_daily",
        pd.DataFrame({"event_time": ["2026-09-10T00:00:00Z"]}),
        "1.0",
        "UTC",
    )

    assert validate_canonical_batch(no_time, request) == "schema_mismatch"
    assert validate_canonical_batch(wrong_kind, request) == "schema_mismatch"
    assert validate_canonical_batch(wrong_schema, request) == "schema_mismatch"
    assert validate_canonical_batch(missing_field, request) == "schema_mismatch"

    dated_request = DatasetRequest(
        "equity_daily",
        start="2026-09-10",
        end="2026-09-11",
        require_complete=True,
    )
    partial = CanonicalBatch(
        "equity_daily",
        pd.DataFrame(
            {
                "instrument": ["SH600000"],
                "event_time": ["2026-09-10T07:00:00Z"],
            }
        ),
        "1.0",
        "UTC",
    )
    assert validate_canonical_batch(partial, dated_request) == "incomplete"


def test_local_file_adapter_reports_unsupported_missing_file_and_format(tmp_path: Path):
    missing_path = tmp_path / "missing.csv"
    source = LocalFileDataSource({"equity_daily": LocalDatasetSpec("equity_daily", missing_path)})

    unsupported = source.fetch_dataset(DatasetRequest("trading_calendar"))
    missing = source.fetch_dataset(DatasetRequest("equity_daily", require_complete=False))

    assert unsupported.status == "unsupported"
    assert missing.status == "provider_error"
    assert missing.error_class == "missing_file"

    odd = tmp_path / "daily.txt"
    odd.write_text("not-a-supported-canonical-format", encoding="utf-8")
    odd_source = LocalFileDataSource({"equity_daily": LocalDatasetSpec("equity_daily", odd)})
    odd_result = odd_source.fetch_dataset(DatasetRequest("equity_daily", require_complete=False))
    assert odd_result.status == "unsupported"
    assert odd_result.error_class == "unsupported_format"


def test_local_file_filters_instruments_dates_and_requested_fields(tmp_path: Path):
    path = tmp_path / "daily.csv"
    pd.DataFrame(
        {
            "instrument": ["SH600000", "SZ000001", "SH600000"],
            "event_time": [
                "2026-09-09T15:00:00+08:00",
                "2026-09-10T15:00:00+08:00",
                "2026-09-11T15:00:00+08:00",
            ],
            "close": [9.9, 20.0, 10.1],
            "volume": [100.0, 200.0, 300.0],
            "ignored": [1, 2, 3],
        }
    ).to_csv(path, index=False)
    source = LocalFileDataSource({"equity_daily": LocalDatasetSpec("equity_daily", path)})
    request = DatasetRequest(
        "equity_daily",
        instruments=("SH600000",),
        start="2026-09-11",
        end="2026-09-11",
        fields=("close", "volume"),
    )

    result = source.fetch_dataset(request)

    assert result.status == "success"
    assert result.batch is not None
    assert result.batch.data["instrument"].tolist() == ["SH600000"]
    assert result.batch.data["close"].tolist() == [10.1]
    assert "ignored" not in result.batch.data.columns


def test_recorded_adapter_rejects_unsupported_and_unrecorded_requests():
    capabilities = SourceCapabilities(
        "recorded",
        (DatasetCapability("equity_daily"), DatasetCapability("trading_calendar")),
    )
    source = RecordedDataSource({}, capabilities)

    unsupported = source.fetch_dataset(DatasetRequest("equity_daily", schema_version="2.0"))
    unrecorded = source.fetch_dataset(DatasetRequest("trading_calendar"))

    assert unsupported.status == "unsupported"
    assert unrecorded.status == "unsupported"
    assert unrecorded.error_class == "unrecorded_request"


def test_instrument_master_combines_listed_and_delisted_without_duplicate_symbols():
    listed = pd.DataFrame(
        {
            "ts_code": ["600000.SH", "000001.SZ"],
            "symbol": ["600000", "000001"],
            "name": ["A", "B"],
            "area": ["SH", "SZ"],
            "industry": ["Bank", "Bank"],
            "market": ["Main", "Main"],
            "exchange": ["SSE", "SZSE"],
            "list_status": ["L", "L"],
            "list_date": ["19991110", "19910403"],
            "delist_date": [None, None],
            "is_hs": ["H", "S"],
            "act_name": [None, None],
            "act_ent_type": [None, None],
        }
    )
    delisted = listed.iloc[[0]].copy()
    delisted.loc[:, "list_status"] = "D"
    client = _StubClient(
        {"stock_basic": [FetchResult(listed, "success", 1), FetchResult(delisted, "success", 2)]}
    )
    source = TushareSemanticDataSource(client)
    request = DatasetRequest("instrument_master", require_complete=False)

    result = source.fetch_dataset(request)

    assert result.status == "success"
    assert result.attempts == 3
    assert result.batch is not None
    assert result.batch.timezone == "UTC"
    assert result.batch.data["instrument"].tolist() == ["SZ000001", "SH600000"]
    assert "ts_code" not in result.batch.data.columns
    assert [call[1]["list_status"] for call in client.calls] == ["L", "D"]


def test_instrument_master_fails_closed_for_provider_error_and_schema_drift():
    failure_source = TushareSemanticDataSource(
        _StubClient(
            {
                "stock_basic": [
                    FetchResult(pd.DataFrame(), "failed", 3, "provider unavailable"),
                    FetchResult(pd.DataFrame(), "empty", 1),
                ]
            }
        )
    )
    failed = failure_source.fetch_dataset(DatasetRequest("instrument_master", require_complete=False))
    assert failed.status == "provider_error"

    bad = pd.DataFrame({"ts_code": ["600000.SH"], "list_status": ["L"]})
    schema_source = TushareSemanticDataSource(
        _StubClient({"stock_basic": [FetchResult(bad, "success", 1), FetchResult(pd.DataFrame(), "empty", 1)]})
    )
    schema = schema_source.fetch_dataset(DatasetRequest("instrument_master", require_complete=False))
    assert schema.status == "schema_mismatch"
    assert "list_date" in (schema.error or "")


def test_trading_calendar_canonicalizes_dates_and_open_flag_with_range_params():
    raw = pd.DataFrame(
        {
            "exchange": ["SSE", "SSE"],
            "cal_date": ["20260910", "20260911"],
            "is_open": [1, 0],
            "pretrade_date": ["20260909", "20260910"],
        }
    )
    client = _StubClient({"trade_cal": FetchResult(raw, "success", 1)})
    source = TushareSemanticDataSource(client)
    request = DatasetRequest(
        "trading_calendar",
        start="2026-09-10",
        end="2026-09-11",
    )

    result = source.fetch_dataset(request)

    assert result.status == "success"
    assert result.batch is not None
    assert result.batch.data["is_open"].tolist() == [True, False]
    assert result.batch.data["previous_trading_date"].tolist() == ["2026-09-09", "2026-09-10"]
    _, params = client.calls[0]
    assert params["start_date"] == "20260910"
    assert params["end_date"] == "20260911"
    assert params["exchange"] == ""


def test_trading_calendar_fails_closed_for_error_and_schema_drift():
    failed_source = TushareSemanticDataSource(
        _StubClient({"trade_cal": FetchResult(pd.DataFrame(), "failed", 2, "timeout")})
    )
    failed = failed_source.fetch_dataset(
        DatasetRequest("trading_calendar", start="2026-09-10", end="2026-09-11")
    )
    assert failed.status == "timeout"

    bad_source = TushareSemanticDataSource(
        _StubClient({"trade_cal": FetchResult(pd.DataFrame({"cal_date": ["20260910"]}), "success", 1)})
    )
    bad = bad_source.fetch_dataset(
        DatasetRequest("trading_calendar", start="2026-09-10", end="2026-09-11")
    )
    assert bad.status == "schema_mismatch"
    assert "is_open" in (bad.error or "")


def test_daily_basic_and_adjustment_schema_errors_are_explicit():
    daily_basic = TushareSemanticDataSource(
        _StubClient(
            {
                "daily_basic": FetchResult(
                    pd.DataFrame({"ts_code": ["600000.SH"], "trade_date": ["20260910"]}),
                    "success",
                    1,
                )
            }
        )
    )
    basic_result = daily_basic.fetch_dataset(
        DatasetRequest("equity_daily_basic", start="2026-09-10", end="2026-09-10")
    )
    assert basic_result.status == "schema_mismatch"

    adjustment = TushareSemanticDataSource(
        _StubClient(
            {
                "adj_factor": FetchResult(
                    pd.DataFrame({"ts_code": ["600000.SH"], "trade_date": ["20260910"]}),
                    "success",
                    1,
                )
            }
        )
    )
    adjustment_result = adjustment.fetch_dataset(
        DatasetRequest("adjustment_factor", start="2026-09-10", end="2026-09-10")
    )
    assert adjustment_result.status == "schema_mismatch"


def test_required_non_success_envelope_raises_contract_error():
    request = DatasetRequest("equity_daily")
    envelope = FetchEnvelope(
        "fixture",
        "permission_denied",
        1,
        error="no entitlement",
        error_class="permission_denied",
    )
    from qlib_platform.data.sources.semantic import require_usable

    with pytest.raises(DataSourceContractError, match="permission_denied"):
        require_usable(envelope, request)
