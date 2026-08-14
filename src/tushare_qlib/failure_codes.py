from __future__ import annotations

from enum import Enum


class FailureCode(str, Enum):
    DATA_NOT_READY = "DATA_NOT_READY"
    DAILY_SYNC_FAILED = "DAILY_SYNC_FAILED"
    MODEL_NOT_DEPLOYED = "MODEL_NOT_DEPLOYED"
    MODEL_LOAD_FAILED = "MODEL_LOAD_FAILED"
    SIGNAL_REJECTED = "SIGNAL_REJECTED"
    STALE_SIGNAL = "STALE_SIGNAL"
    PIPELINE_FAILED = "PIPELINE_FAILED"


def classify_failure(exc: BaseException, phase: str) -> FailureCode:
    """Map research-pipeline exceptions to stable, non-sensitive failure codes."""

    name = type(exc).__name__
    message = str(exc).lower()
    normalized_phase = phase.upper()
    if name == "SignalRejectedError":
        return FailureCode.SIGNAL_REJECTED
    if normalized_phase == "SYNC":
        return FailureCode.DAILY_SYNC_FAILED
    if "no deployed model" in message or "requires a deployed model" in message:
        return FailureCode.MODEL_NOT_DEPLOYED
    if "model bundle" in message or "bundle checksum" in message or "parity validation" in message:
        return FailureCode.MODEL_LOAD_FAILED
    if "exactly one pass signal" in message or "stale signal" in message:
        return FailureCode.STALE_SIGNAL
    if name == "FileNotFoundError" and (
        "dataset" in message or "calendar" in message or "daily" in message or "feature" in message
    ):
        return FailureCode.DATA_NOT_READY
    return FailureCode.PIPELINE_FAILED
