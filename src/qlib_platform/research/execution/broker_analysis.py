from __future__ import annotations

import math

import numpy as np
import pandas as pd


def analyze_broker_events(events: pd.DataFrame) -> dict[str, float | int]:
    required = {"order_id", "status", "requested_quantity", "filled_quantity"}
    missing = sorted(required.difference(events.columns))
    if missing:
        raise ValueError(f"broker events missing required columns: {missing}")
    if events.empty:
        raise ValueError("broker events must be non-empty")

    frame = events.copy()
    frame["order_id"] = frame["order_id"].astype(str)
    frame["status"] = frame["status"].astype(str).str.upper()
    if bool(frame["order_id"].str.strip().eq("").any()):
        raise ValueError("broker events contain blank order_id")
    for column in ("requested_quantity", "filled_quantity"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
        if bool(frame[column].isna().any()) or bool((frame[column] < 0).any()):
            raise ValueError(f"{column} must be numeric and non-negative")
    if bool((frame["filled_quantity"] > frame["requested_quantity"]).any()):
        raise ValueError("filled_quantity must not exceed requested_quantity")

    requested = float(frame["requested_quantity"].sum())
    filled = float(frame["filled_quantity"].sum())
    partial_mask = (frame["filled_quantity"] > 0) & (
        frame["filled_quantity"] < frame["requested_quantity"]
    )
    result: dict[str, float | int] = {
        "event_count": int(len(frame)),
        "order_count": int(frame["order_id"].nunique()),
        "requested_quantity": requested,
        "filled_quantity": filled,
        "fill_ratio": filled / requested if requested > 0 else 0.0,
        "reject_count": int(frame["status"].str.contains("REJECT", regex=False).sum()),
        "cancel_count": int(frame["status"].str.contains("CANCEL", regex=False).sum()),
        "partial_fill_count": int(partial_mask.sum()),
    }

    if {"submitted_at", "ack_at"}.issubset(frame.columns):
        submitted = pd.to_datetime(frame["submitted_at"], errors="coerce")
        acknowledged = pd.to_datetime(frame["ack_at"], errors="coerce")
        valid = submitted.notna() & acknowledged.notna()
        if bool(valid.any()):
            latency_ms = (acknowledged.loc[valid] - submitted.loc[valid]).dt.total_seconds() * 1_000.0
            if bool((latency_ms < 0).any()):
                raise ValueError("ack_at must not precede submitted_at")
            result["ack_latency_p50_ms"] = float(np.percentile(latency_ms.to_numpy(dtype=float), 50))
            result["ack_latency_p95_ms"] = float(np.percentile(latency_ms.to_numpy(dtype=float), 95))

    slippage_values: list[float] = []
    if {"side", "reference_price", "fill_price"}.issubset(frame.columns):
        for _, row in frame.iterrows():
            if float(row["filled_quantity"]) <= 0 or pd.isna(row["fill_price"]):
                continue
            side = str(row["side"]).upper()
            if side not in {"BUY", "SELL"}:
                raise ValueError("side must be BUY or SELL when slippage fields are supplied")
            reference = float(row["reference_price"])
            fill_price = float(row["fill_price"])
            if not math.isfinite(reference) or not math.isfinite(fill_price) or reference <= 0 or fill_price <= 0:
                raise ValueError("reference_price and fill_price must be finite and positive")
            direction = 1.0 if side == "BUY" else -1.0
            slippage_values.append(direction * (fill_price - reference) / reference * 10_000.0)
    if slippage_values:
        result["mean_signed_slippage_bps"] = float(sum(slippage_values) / len(slippage_values))
    return result
