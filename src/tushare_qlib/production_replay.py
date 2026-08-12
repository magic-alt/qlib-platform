from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .live_inference import run_live_inference
from .pretrade_runner import run_pretrade_actions
from .settings import Settings


def _open_dates(settings: Settings, start: str, end: str) -> list[str]:
    path = settings.paths.metadata / "trade_calendar.parquet"
    if not path.is_file():
        raise FileNotFoundError(f"official trade calendar is missing: {path}")
    calendar = pd.read_parquet(path)
    dates = pd.to_datetime(
        calendar.loc[pd.to_numeric(calendar["is_open"], errors="coerce") == 1, "cal_date"],
        errors="coerce",
    ).dropna()
    selected = dates.loc[(dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))]
    return [str(value) for value in selected.sort_values().dt.strftime("%Y-%m-%d").tolist()]


def run_production_replay(
    settings: Settings,
    *,
    start: str,
    end: str,
    deployment_id: str | None = None,
    with_pretrade: bool = False,
) -> Path:
    rows: list[dict[str, Any]] = []
    for signal_date in _open_dates(settings, start, end):
        try:
            inference = run_live_inference(
                settings,
                as_of=signal_date,
                deployment_id=deployment_id,
                require_daily_sync=False,
            )
            row: dict[str, Any] = {
                "signalDate": signal_date,
                "tradeDate": inference.trade_date,
                "signalId": inference.signal_id,
                "health": inference.health.decision,
                "reasons": inference.health.reasons,
                "created": inference.created,
            }
            inbox = settings.paths.root / "inbox" / "pretrade" / inference.trade_date
            if with_pretrade and inbox.is_dir() and inference.health.passed:
                action = run_pretrade_actions(settings, trade_date=inference.trade_date, notify=False)
                row["pretrade"] = {
                    "decision": str(action.decision_path),
                    "orders": str(action.orders_path),
                    "blocked": str(action.blocked_path),
                }
            rows.append(row)
        except Exception as exc:
            rows.append(
                {
                    "signalDate": signal_date,
                    "health": "FAILED",
                    "errorCode": type(exc).__name__,
                }
            )
    payload = {
        "schemaVersion": "1.0",
        "start": start,
        "end": end,
        "deploymentId": deployment_id,
        "createdAtUtc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "runs": rows,
        "passed": bool(rows) and all(row.get("health") == "PASS" for row in rows),
    }
    root = settings.paths.output / "replay"
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"replay_{pd.Timestamp(start):%Y%m%d}_{pd.Timestamp(end):%Y%m%d}.json"
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.", dir=root)
    os.close(fd)
    temporary = Path(name)
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target
