from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from qlib_platform.data.sources.base import FetchResult
from qlib_platform.data.sources.semantic import (
    CanonicalBatch,
    Coverage,
    DataSourceContractError,
    DatasetCapability,
    DatasetRequest,
    FetchEnvelope,
    LocalDatasetSpec,
    LocalFileDataSource,
    RecordedDataSource,
    SourceCapabilities,
    file_sha256,
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


def _daily_raw() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": ["600000.SH", "000001.SZ"],
            "trade_date": ["20260910", "20260911"],
            "open": [10.0, 20.0],
            "high": [10.5, 20.5],
            "low": [9.8, 19.5],
            "close": [10.2, 20.1],
            "pre_close": [9.9, 19.8],
            "change": [0.3, 0.3],
            "pct_chg": [3.03, 1.52],
            "vol": [12.0, 34.0],
            "amount": [123.4, 567.8],
        }
    )


def _daily_request(**kwargs: Any) -> DatasetRequest:
    values: dict[str, Any] = {
        "dataset_kind": "equity_daily",
        "start": "2026-09-10",
        "end": "2026-09-11",
    }
    values.update(kwargs)
    return DatasetRequest(**values)


def test_tushare_and_immutable_file_adapters_produce_identical_canonical_daily(tmp_path):
    raw = _daily_raw()
    source = TushareSemanticDataSource(_StubClient({"daily": FetchResult(raw, "success", 1)}))
    request = _daily_request()

    provider_envelope = source.fetch_dataset(request)
    provider_batch = require_usable(provider_envelope, request)

    assert provider_batch.canonical_units["volume"] == "share"
    assert provider_batch.canonical_units["turnover"] == "CNY"
    assert provider_batch.source_units == {"vol": "hand", "amount": "CNY_thousand"}
    assert provider_batch.data["instrument"].tolist() == ["SH600000", "SZ000001"]
    assert provider_batch.data["volume"].tolist() == [1200.0, 3400.0]
    assert provider_batch.data["turnover"].tolist() == [123400.0, 567800.0]
    assert str(provider_batch.data["event_time"].dt.tz) == "Asia/Shanghai"
    assert provider_batch.coverage is not None
    assert provider_batch.coverage.complete is True

    path = tmp_path / "equity_daily.parquet"
    provider_batch.data.to_parquet(path, index=False)
    file_source = LocalFileDataSource(
        {
            "equity_daily": LocalDatasetSpec(
                dataset_kind="equity_daily",
                path=path,
                timezone="Asia/Shanghai",
                canonical_units=provider_batch.canonical_units,
                expected_sha256=file_sha256(path),
            )
        }
    )
    file_batch = require_usable(file_source.fetch_dataset(request), request)

    pd.testing.assert_frame_equal(file_batch.data, provider_batch.data)
    assert file_batch.coverage == provider_batch.coverage


def test_tushare_daily_basic_converts_shares_value_and_percent_units():
    raw = pd.DataFrame(
        {
            "ts_code": ["600000.SH"],
            "trade_date": ["20260910"],
            "total_share": [100.0],
            "float_share": [80.0],
            "free_share": [60.0],
            "total_mv": [250.0],
            "circ_mv": [200.0],
            "turnover_rate": [2.5],
            "turnover_rate_f": [3.0],
            "dv_ratio": [1.2],
            "dv_ttm": [1.0],
        }
    )
    source = TushareSemanticDataSource(_StubClient({"daily_basic": FetchResult(raw, "success", 1)}))
    request = DatasetRequest(
        "equity_daily_basic",
        start="2026-09-10",
        end="2026-09-10",
    )

    batch = require_usable(source.fetch_dataset(request), request)

    row = batch.data.iloc[0]
    assert row["total_shares"] == 1_000_000.0
    assert row["float_shares"] == 800_000.0
    assert row["free_shares"] == 600_000.0
    assert row["total_market_value"] == 2_500_000.0
    assert row["circulating_market_value"] == 2_000_000.0
    assert row["turnover_rate"] == 0.025
    assert row["dv_ratio"] == 0.012


def test_missing_price_remains_missing_in_canonical_daily():
    raw = _daily_raw().iloc[[0]].copy()
    raw.loc[:, "close"] = float("nan")
    source = TushareSemanticDataSource(_StubClient({"daily": FetchResult(raw, "success", 1)}))
    request = DatasetRequest("equity_daily", start="2026-09-10", end="2026-09-10")

    batch = require_usable(source.fetch_dataset(request), request)

    assert pd.isna(batch.data.loc[0, "close"])
    assert batch.data.loc[0, "close"] != 0


def test_adjustment_mode_is_negotiated_instead_of_silently_reinterpreted():
    source = TushareSemanticDataSource(_StubClient({}))

    unsupported = source.fetch_dataset(_daily_request(adjustment="qfq"))

    assert unsupported.status == "unsupported"
    assert unsupported.error_class == "unsupported"


def test_adjustment_factor_is_an_explicit_canonical_dataset():
    raw = pd.DataFrame(
        {"ts_code": ["600000.SH"], "trade_date": ["20260910"], "adj_factor": [1.2345]}
    )
    source = TushareSemanticDataSource(_StubClient({"adj_factor": FetchResult(raw, "success", 1)}))
    request = DatasetRequest("adjustment_factor", start="2026-09-10", end="2026-09-10")

    batch = require_usable(source.fetch_dataset(request), request)

    assert batch.data.loc[0, "adjustment_factor"] == pytest.approx(1.2345)
    assert batch.canonical_units["adjustment_factor"] == "dimensionless"


def test_provider_schema_drift_is_auditable_and_not_auto_repaired():
    raw = _daily_raw().drop(columns=["vol"])
    source = TushareSemanticDataSource(_StubClient({"daily": FetchResult(raw, "success", 1)}))

    envelope = source.fetch_dataset(_daily_request())

    assert envelope.status == "schema_mismatch"
    assert envelope.error_class == "missing_columns"
    assert "vol" in (envelope.error or "")


def test_duplicate_canonical_keys_fail_as_conflict():
    raw = pd.concat([_daily_raw().iloc[[0]], _daily_raw().iloc[[0]]], ignore_index=True)
    source = TushareSemanticDataSource(_StubClient({"daily": FetchResult(raw, "success", 1)}))
    request = DatasetRequest("equity_daily", start="2026-09-10", end="2026-09-10")

    envelope = source.fetch_dataset(request)

    assert envelope.status == "conflict"
    with pytest.raises(DataSourceContractError, match="conflict"):
        require_usable(envelope, request)


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (FetchResult(pd.DataFrame(), "permission_denied", 1, "permission denied"), "permission_denied"),
        (FetchResult(pd.DataFrame(), "failed", 3, "HTTP 429 rate limit"), "rate_limited"),
        (FetchResult(pd.DataFrame(), "failed", 3, "request timed out"), "timeout"),
        (FetchResult(pd.DataFrame(), "failed", 3, "provider unavailable"), "provider_error"),
    ],
)
def test_provider_failures_keep_distinct_error_classification(result: FetchResult, expected: str):
    source = TushareSemanticDataSource(_StubClient({"daily": result}))

    envelope = source.fetch_dataset(_daily_request())

    assert envelope.status == expected
    assert envelope.error_class == expected


def test_required_empty_data_is_incomplete_not_successful_empty():
    source = TushareSemanticDataSource(_StubClient({"daily": FetchResult(pd.DataFrame(), "empty", 1)}))

    envelope = source.fetch_dataset(_daily_request())

    assert envelope.status == "incomplete"
    assert envelope.succeeded is False


def test_local_file_checksum_drift_fails_closed(tmp_path):
    path = tmp_path / "daily.csv"
    pd.DataFrame(
        {
            "instrument": ["SH600000"],
            "event_time": ["2026-09-10T15:00:00+08:00"],
            "close": [10.0],
        }
    ).to_csv(path, index=False)
    source = LocalFileDataSource(
        {
            "equity_daily": LocalDatasetSpec(
                "equity_daily",
                path,
                expected_sha256="0" * 64,
            )
        }
    )

    envelope = source.fetch_dataset(
        DatasetRequest("equity_daily", start="2026-09-10", end="2026-09-10")
    )

    assert envelope.status == "conflict"
    assert envelope.error_class == "checksum_mismatch"


def test_local_file_required_coverage_rejects_partial_range(tmp_path):
    path = tmp_path / "daily.csv"
    pd.DataFrame(
        {
            "instrument": ["SH600000"],
            "event_time": ["2026-09-10T15:00:00+08:00"],
            "close": [10.0],
        }
    ).to_csv(path, index=False)
    source = LocalFileDataSource({"equity_daily": LocalDatasetSpec("equity_daily", path)})

    envelope = source.fetch_dataset(_daily_request())

    assert envelope.status == "incomplete"


def test_recorded_adapter_replays_offline_failure_without_network():
    request = DatasetRequest("equity_daily", start="2026-09-10", end="2026-09-10")
    capabilities = SourceCapabilities("recorded", (DatasetCapability("equity_daily"),))
    envelope = FetchEnvelope(
        "recorded",
        "rate_limited",
        2,
        error_class="rate_limited",
        error="recorded 429",
    )
    source = RecordedDataSource({"equity_daily": envelope}, capabilities)

    replayed = source.fetch_dataset(request)

    assert replayed is envelope
    assert replayed.status == "rate_limited"


def test_capability_discovery_does_not_imply_entitlement():
    source = TushareSemanticDataSource(
        _StubClient({"daily": FetchResult(pd.DataFrame(), "permission_denied", 1, "permission denied")})
    )
    request = DatasetRequest("equity_daily", start="2026-09-10", end="2026-09-10")

    capability = source.capabilities.negotiate(request)
    envelope = source.fetch_dataset(request)

    assert capability.dataset_kind == "equity_daily"
    assert envelope.entitlement == "denied"
    assert envelope.status == "permission_denied"


def test_request_and_schema_negotiation_fail_closed():
    with pytest.raises(ValueError, match="start must not be after end"):
        DatasetRequest("equity_daily", start="2026-09-11", end="2026-09-10")

    capabilities = SourceCapabilities("fixture", (DatasetCapability("equity_daily"),))
    with pytest.raises(DataSourceContractError, match="does not support request"):
        capabilities.negotiate(DatasetRequest("equity_daily", schema_version="2.0"))


def test_require_usable_revalidates_canonical_batch():
    request = DatasetRequest("equity_daily", fields=("close",))
    batch = CanonicalBatch(
        "equity_daily",
        pd.DataFrame({"instrument": ["SH600000"], "event_time": ["2026-09-10T15:00:00+08:00"]}),
        "1.0",
        "Asia/Shanghai",
        coverage=Coverage(
            "2026-09-10T07:00:00+00:00",
            "2026-09-10T07:00:00+00:00",
            1,
            True,
        ),
    )
    envelope = FetchEnvelope("fixture", "success", 1, batch=batch)

    with pytest.raises(DataSourceContractError, match="schema_mismatch"):
        require_usable(envelope, request)
