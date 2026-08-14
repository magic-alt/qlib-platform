from __future__ import annotations

from tushare_qlib.failure_codes import FailureCode, classify_failure


def test_failure_codes_are_stable_research_business_codes():
    assert (
        classify_failure(RuntimeError("no DEPLOYED model is registered"), "INFERENCE")
        is FailureCode.MODEL_NOT_DEPLOYED
    )
    assert (
        classify_failure(FileNotFoundError("dataset manifest missing"), "INFERENCE")
        is FailureCode.DATA_NOT_READY
    )
    assert classify_failure(ValueError("stale signal"), "INFERENCE") is FailureCode.STALE_SIGNAL
    assert classify_failure(TimeoutError(), "SYNC") is FailureCode.DAILY_SYNC_FAILED
