from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from .artifact_resolver import ArtifactResolver
from .artifacts import ArtifactType
from .daily_signal_runner import _delivery_service, _failure_envelope, feishu_notifier_from_environment
from .execution import build_topk_orders
from .freshness import validate_execution_snapshot
from .holdings_ledger import reconcile_holdings
from .live_artifacts import validate_live_artifact
from .notifier import NotificationEnvelope
from .ops_state import OpsState, RunStatus
from .settings import Settings


@dataclass(frozen=True)
class PretradeResult:
    signal_id: str
    trade_date: str
    decision_path: Path
    orders_path: Path
    blocked_path: Path


def artifact_resolver(settings: Settings) -> ArtifactResolver:
    return ArtifactResolver(
        roots={
            "research": settings.paths.output / "research",
            "deployment": settings.paths.models / "deployments",
            "signal": settings.paths.output / "live",
        }
    )


def _required_inbox(settings: Settings, trade_date: str) -> Path:
    root = settings.paths.root / "inbox" / "pretrade" / trade_date
    required = [root / "positions.csv", root / "quotes.csv", root / "account.json"]
    missing = [path.name for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"pretrade inbox is incomplete for {trade_date}: {missing}")
    return root


def _load_signal(settings: Settings, state: OpsState, trade_date: str) -> tuple[dict[str, Any], pd.DataFrame]:
    record = state.signal_for_trade_date(trade_date)
    resolver = artifact_resolver(settings)
    manifest_path = resolver.resolve(str(record["manifest_uri"]))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    score_path = manifest_path.parent / str(manifest["artifacts"]["score"]["path"])
    scores = pd.read_parquet(score_path)
    validate_live_artifact(
        scores,
        ArtifactType.MODEL_SCORE,
        resolver=resolver,
        expected_deployment_id=str(record["deployment_id"]),
    )
    return record, scores


def _account_snapshot(account: Mapping[str, Any], trade_date: str, max_age_seconds: int) -> None:
    frame = pd.DataFrame(
        {
            "as_of_trade_date": [account.get("as_of_trade_date")],
            "snapshot_at_utc": [account.get("snapshot_at_utc")],
        }
    )
    validate_execution_snapshot(
        frame,
        name="account",
        trade_date=trade_date,
        max_age_seconds=max_age_seconds,
    )


def _lines(frame: pd.DataFrame, action: str) -> list[str]:
    if frame.empty:
        return []
    if "side" in frame.columns:
        selected = frame.loc[frame["side"] == action]
        return [f"{row.instrument} {int(row.quantity)}股 | {row.action_reason}" for row in selected.itertuples()]
    selected = frame.loc[frame["target_action"] == action]
    return [f"{row.instrument} | {row.action_reason}" for row in selected.itertuples()]


def _action_envelope(
    record: Mapping[str, Any], decision: pd.DataFrame, orders: pd.DataFrame, blocked: pd.DataFrame
) -> NotificationEnvelope:
    blocked_lines = (
        [f"{row.instrument} | {row.reason}" for row in blocked.itertuples()]
        if not blocked.empty
        else []
    )
    payload = str(record["signal_sha256"])
    message_hash = hashlib.sha256(
        f"{payload}|{len(orders)}|{len(blocked)}".encode()
    ).hexdigest()[:16]
    return NotificationEnvelope(
        message_id=f"pretrade-{record['signal_id']}-{message_hash}",
        message_kind="PRETRADE_ACTION",
        business_date=str(record["trade_date"]),
        trade_date=str(record["trade_date"]),
        channel="feishu",
        title=f"Pretrade Action | {record['trade_date']}",
        summary=f"signal_id={record['signal_id']}\n人工确认后执行；系统未提交任何订单。",
        deployment_id=str(record["deployment_id"]),
        signal_sha256=payload,
        sections={
            "SELL": _lines(orders, "SELL") or ["无"],
            "BUY": _lines(orders, "BUY") or ["无"],
            "HOLD": _lines(decision, "HOLD") or ["无"],
            "BLOCKED": blocked_lines or ["无"],
        },
    )


def run_pretrade_actions(
    settings: Settings,
    *,
    trade_date: str,
    notify: bool = True,
) -> PretradeResult:
    state = OpsState(settings.paths.state / "ops.sqlite3")
    run_id = f"pretrade-{trade_date}-{uuid.uuid4().hex[:12]}"
    state.start_run(run_id, "PRETRADE", trade_date)
    notifier = None
    service = _delivery_service(settings, state)
    try:
        notifier = feishu_notifier_from_environment() if notify else None
        record, scores = _load_signal(settings, state, trade_date)
        inbox = _required_inbox(settings, trade_date)
        positions_raw = pd.read_csv(inbox / "positions.csv")
        quotes = pd.read_csv(inbox / "quotes.csv")
        account = json.loads((inbox / "account.json").read_text(encoding="utf-8"))
        required_account = {
            "as_of_trade_date",
            "snapshot_at_utc",
            "portfolio_value",
            "cash",
            "daily_pnl_pct",
        }
        missing_account = required_account - set(account)
        if missing_account:
            raise ValueError(f"account snapshot missing fields: {sorted(missing_account)}")
        execution = settings.data.get("execution", {})
        execution = execution if isinstance(execution, Mapping) else {}
        _account_snapshot(account, trade_date, int(execution.get("max_position_age_seconds", 300)))
        fills_path = inbox / "fills.csv"
        initial_path = inbox / "initial_holdings.csv"
        positions = reconcile_holdings(
            positions_raw,
            pd.read_csv(fills_path) if fills_path.is_file() else None,
            as_of_date=trade_date,
            calendar_path=settings.paths.metadata / "trade_calendar.parquet",
            ledger_path=settings.paths.state / "holdings_ledger.parquet",
            initial_holdings=pd.read_csv(initial_path) if initial_path.is_file() else None,
        )
        decision, orders, blocked = build_topk_orders(
            scores,
            positions,
            quotes,
            signal_date=str(record["signal_date"]),
            trade_date=trade_date,
            cash=float(account["cash"]),
            daily_pnl_pct=float(account["daily_pnl_pct"]),
            artifact_resolver=artifact_resolver(settings),
        )
        output = settings.paths.output / "live" / str(record["signal_id"]) / "pretrade"
        output.mkdir(parents=True, exist_ok=True)
        decision_path = output / "strategy_decision.parquet"
        orders_path = output / "order_intent.parquet"
        blocked_path = output / "blocked.csv"
        decision.to_parquet(decision_path, index=False)
        orders.to_parquet(orders_path, index=False)
        blocked.to_csv(blocked_path, index=False)
        if notifier is not None:
            service.deliver(
                _action_envelope(record, decision, orders, blocked),
                notifier,
            )
        state.finish_run(
            run_id,
            RunStatus.PASS,
            {"signalId": record["signal_id"], "orders": len(orders), "blocked": len(blocked)},
        )
        return PretradeResult(
            signal_id=str(record["signal_id"]),
            trade_date=trade_date,
            decision_path=decision_path,
            orders_path=orders_path,
            blocked_path=blocked_path,
        )
    except Exception as exc:
        state.finish_run(run_id, RunStatus.FAILED, {"errorCode": type(exc).__name__})
        if notifier is not None:
            service.deliver(_failure_envelope(trade_date, "PRETRADE", type(exc).__name__), notifier)
        raise
