from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
import yaml

from .broker import ReadOnlyBrokerAdapter
from .market_snapshot import MarketSnapshotProvider
from .ops_state import OpsState
from .pretrade_runner import PretradeResult, run_pretrade_actions
from .settings import Settings


@dataclass(frozen=True)
class ShadowResult:
    trade_date: str
    signal_id: str
    root: Path
    events_path: Path
    metrics_path: Path
    summary_path: Path


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(
            json.dumps(dict(payload), ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _load_shadow_config(path: str | Path | None) -> Mapping[str, Any]:
    if path is None:
        return {}
    config_path = Path(path).expanduser().resolve()
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, Mapping):
        raise ValueError("shadow config must be a mapping")
    return payload


def simulate_order_lifecycle(
    orders: pd.DataFrame, *, trade_date: str, fill_ratio: float = 1.0
) -> pd.DataFrame:
    if not 0 < fill_ratio <= 1:
        raise ValueError("shadow fill_ratio must be in (0, 1]")
    if orders.empty:
        return pd.DataFrame(
            columns=[
                "event_id",
                "client_order_id",
                "event_sequence",
                "event_type",
                "trade_date",
                "instrument",
                "side",
                "quantity",
                "fill_quantity",
                "fill_price",
            ]
        )
    required = {"client_order_id", "instrument", "side", "quantity", "limit_price"}
    missing = required - set(orders.columns)
    if missing:
        raise ValueError(f"shadow order intent missing fields: {sorted(missing)}")
    rows: list[dict[str, Any]] = []
    for order in orders.sort_values("client_order_id").to_dict("records"):
        quantity = int(order["quantity"])
        filled = int(quantity * fill_ratio)
        if filled <= 0:
            filled = quantity
        for sequence, event_type in enumerate(("INTENT_CREATED", "SIM_ACCEPTED", "SIM_FILLED"), 1):
            identity = f"{order['client_order_id']}|{sequence}|{event_type}".encode()
            rows.append(
                {
                    "event_id": hashlib.sha256(identity).hexdigest()[:24],
                    "client_order_id": str(order["client_order_id"]),
                    "event_sequence": sequence,
                    "event_type": event_type,
                    "trade_date": trade_date,
                    "instrument": str(order["instrument"]),
                    "side": str(order["side"]),
                    "quantity": quantity,
                    "fill_quantity": filled if event_type == "SIM_FILLED" else 0,
                    "fill_price": float(order["limit_price"]) if event_type == "SIM_FILLED" else None,
                }
            )
    return pd.DataFrame(rows)


def _cumulative_summary(root: Path) -> dict[str, Any]:
    totals = {"days": 0, "orders": 0, "fills": 0, "blocked": 0, "grossNotional": 0.0}
    for path in sorted(root.glob("*/metrics.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        totals["days"] += 1
        totals["orders"] += int(payload["orderCount"])
        totals["fills"] += int(payload["filledCount"])
        totals["blocked"] += int(payload["blockedCount"])
        totals["grossNotional"] += float(payload["grossNotional"])
    totals["grossNotional"] = round(float(totals["grossNotional"]), 2)
    return totals


def run_shadow(
    settings: Settings,
    *,
    trade_date: str,
    config_path: str | Path | None = None,
    broker_adapter: ReadOnlyBrokerAdapter | None = None,
    market_provider: MarketSnapshotProvider | None = None,
) -> ShadowResult:
    config = _load_shadow_config(config_path)
    fill_ratio = float(config.get("fill_ratio", 1.0))
    shadow_state_root = settings.paths.state / "shadow"
    root = settings.paths.output / "shadow" / trade_date
    pretrade: PretradeResult = run_pretrade_actions(
        settings,
        trade_date=trade_date,
        notify=False,
        broker_adapter=broker_adapter,
        market_provider=market_provider,
        ops_state=OpsState(shadow_state_root / "ops.sqlite3"),
        signal_state=OpsState(settings.paths.state / "ops.sqlite3"),
        holdings_ledger_path=shadow_state_root / "holdings_ledger.parquet",
        output_dir=root / "pretrade",
    )
    orders = pd.read_parquet(pretrade.orders_path)
    try:
        blocked = pd.read_csv(pretrade.blocked_path)
    except pd.errors.EmptyDataError:
        blocked = pd.DataFrame()
    events = simulate_order_lifecycle(orders, trade_date=trade_date, fill_ratio=fill_ratio)
    root.mkdir(parents=True, exist_ok=True)
    events_path = root / "order_events.parquet"
    events.to_parquet(events_path, index=False)
    filled = events.loc[events["event_type"] == "SIM_FILLED"]
    metrics = {
        "schemaVersion": "1.0",
        "tradeDate": trade_date,
        "signalId": pretrade.signal_id,
        "mode": "SHADOW",
        "brokerSubmitEnabled": False,
        "orderCount": int(len(orders)),
        "filledCount": int(len(filled)),
        "blockedCount": int(len(blocked)),
        "grossNotional": round(float(orders.get("estimated_notional", pd.Series(dtype=float)).sum()), 2),
        "estimatedFees": round(float(orders.get("estimated_fees", pd.Series(dtype=float)).sum()), 2),
        "pretrade": asdict(pretrade),
    }
    metrics_path = root / "metrics.json"
    _atomic_json(metrics_path, metrics)
    summary_path = settings.paths.output / "shadow" / "summary.json"
    _atomic_json(summary_path, _cumulative_summary(settings.paths.output / "shadow"))
    return ShadowResult(trade_date, pretrade.signal_id, root, events_path, metrics_path, summary_path)
