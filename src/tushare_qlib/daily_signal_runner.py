from __future__ import annotations

import hashlib
import os
import uuid
from typing import Any, Mapping

import pandas as pd

from .daily_sync import run_daily_sync
from .delivery_ledger import DeliveryService
from .live_inference import LiveInferenceResult, run_live_inference
from .notifier import FeishuNotifier, NotificationEnvelope
from .ops_state import OpsState, RunStatus
from .settings import Settings


class SignalRejectedError(RuntimeError):
    pass


def feishu_notifier_from_environment() -> FeishuNotifier:
    url = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
    secret = os.getenv("FEISHU_WEBHOOK_SECRET", "").strip() or None
    return FeishuNotifier(url, secret=secret)


def _delivery_service(settings: Settings, state: OpsState) -> DeliveryService:
    production = settings.data.get("production", {})
    production = production if isinstance(production, Mapping) else {}
    notification = production.get("notification", {})
    notification = notification if isinstance(notification, Mapping) else {}
    delays = notification.get("retry_delays_seconds", [2, 10, 30])
    return DeliveryService(state, retry_delays=[float(value) for value in delays])


def _preview_envelope(result: LiveInferenceResult) -> NotificationEnvelope:
    topk = pd.read_csv(result.topk_path)
    names = [f"{row.instrument}  rank={int(row.score_rank)}  score={float(row.score):.6f}" for row in topk.itertuples()]
    status = "PASS" if result.health.passed else "REJECTED"
    return NotificationEnvelope(
        message_id=f"close-{result.signal_id}",
        message_kind="SIGNAL_PREVIEW" if result.health.passed else "SIGNAL_REJECTED",
        business_date=result.signal_date,
        trade_date=result.trade_date,
        channel="feishu",
        title=f"T+1 Signal {status} | {result.signal_date}",
        summary=(
            f"deployment={result.deployment_id}\n"
            f"signal_date={result.signal_date} → trade_date={result.trade_date}\n"
            f"health={result.health.decision}"
        ),
        deployment_id=result.deployment_id,
        signal_sha256=str(pd.read_parquet(result.score_path)["payload_sha256"].iloc[0]),
        sections={"TopK": names, "Health": result.health.reasons or ["PASS"]},
    )


def _failure_envelope(business_date: str, phase: str, error_code: str) -> NotificationEnvelope:
    fingerprint = hashlib.sha256(f"{business_date}|{phase}|{error_code}".encode()).hexdigest()[:24]
    return NotificationEnvelope(
        message_id=f"failure-{fingerprint}",
        message_kind="PIPELINE_FAILED",
        business_date=business_date,
        trade_date=business_date,
        channel="feishu",
        title=f"Production Pipeline FAILED | {business_date}",
        summary=f"phase={phase}\nerror_code={error_code}\n系统已 fail closed，未发布可执行提醒。",
        sections={"Action": ["检查本地 pipeline run 与 delivery ledger", "修复后重跑同一 business date"]},
    )


def run_daily_signal(
    settings: Settings,
    *,
    as_of: str,
    notify: bool = True,
    skip_sync: bool = False,
    supersede: bool = False,
) -> LiveInferenceResult:
    state = OpsState(settings.paths.state / "ops.sqlite3")
    run_id = f"close-{as_of}-{uuid.uuid4().hex[:12]}"
    state.start_run(run_id, "CLOSE", as_of)
    notifier = None
    delivery = _delivery_service(settings, state)
    try:
        notifier = feishu_notifier_from_environment() if notify else None
        if not skip_sync:
            run_daily_sync(settings, as_of=as_of)
        result = run_live_inference(
            settings,
            as_of=as_of,
            require_daily_sync=not skip_sync,
            supersede=supersede,
        )
        if notifier is not None:
            delivery.deliver(_preview_envelope(result), notifier)
        details: dict[str, Any] = {
            "signalId": result.signal_id,
            "deploymentId": result.deployment_id,
            "health": result.health.to_dict(),
        }
        if not result.health.passed:
            state.finish_run(run_id, RunStatus.REJECTED, details)
            raise SignalRejectedError(f"signal health rejected: {','.join(result.health.reasons)}")
        state.finish_run(run_id, RunStatus.PASS, details)
        return result
    except SignalRejectedError:
        raise
    except Exception as exc:
        code = type(exc).__name__
        state.finish_run(run_id, RunStatus.FAILED, {"errorCode": code})
        if notifier is not None:
            delivery.deliver(_failure_envelope(as_of, "CLOSE", code), notifier)
        raise
