from __future__ import annotations

from tushare_qlib.failure_codes import FailureCode, classify_failure
from tushare_qlib.freshness import SnapshotFreshnessError
from tushare_qlib.risk_engine import RiskLimitError


def test_failure_codes_are_stable_business_codes():
    assert classify_failure(RuntimeError("no DEPLOYED model is registered"), "INFERENCE") is FailureCode.MODEL_NOT_DEPLOYED
    assert classify_failure(FileNotFoundError("dataset manifest missing"), "INFERENCE") is FailureCode.DATA_NOT_READY
    assert classify_failure(SnapshotFreshnessError("quotes snapshot is stale"), "PRETRADE") is FailureCode.QUOTE_STALE
    assert classify_failure(SnapshotFreshnessError("positions snapshot is stale"), "PRETRADE") is FailureCode.BROKER_SNAPSHOT_STALE
    assert classify_failure(RiskLimitError("daily loss"), "PRETRADE") is FailureCode.PRETRADE_RISK_REJECTED
    assert classify_failure(TimeoutError(), "SYNC") is FailureCode.DAILY_SYNC_FAILED
