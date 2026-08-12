from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping

import numpy as np
import pandas as pd

from .artifacts import (
    ArtifactContractError,
    ArtifactType,
    PromotionStatus,
    load_artifact_manifest,
    stamp_artifact,
    validate_artifact,
)
from .artifact_resolver import ArtifactResolver
from .live_artifacts import LIVE_ARTIFACT_SCHEMA_VERSION, stamp_live_artifact, validate_live_artifact
from .topk_dropout import TopkDropoutPolicy, topk_dropout_decision
from .freshness import validate_execution_snapshot
from .risk_engine import HardRiskPolicy, pretrade_risk_check


@dataclass(frozen=True)
class ExecutionPolicy:
    board_lot: int = 100
    max_participation_rate: float = 0.05
    commission_rate: float = 0.00025
    min_commission: float = 5.0
    stamp_duty_sell: float = 0.0005
    transfer_fee_rate: float = 0.00001
    price_buffer_buy: float = 0.002
    price_buffer_sell: float = 0.002
    block_limit_up_buy: bool = True
    block_limit_down_sell: bool = True
    max_quote_age_seconds: int = 120
    max_position_age_seconds: int = 300

    @classmethod
    def from_mapping(cls, data: Mapping[str, object] | None) -> "ExecutionPolicy":
        data = data or {}
        return cls(
            board_lot=int(str(data.get("board_lot", cls.board_lot))),
            max_participation_rate=float(str(data.get("max_participation_rate", cls.max_participation_rate))),
            commission_rate=float(str(data.get("commission_rate", cls.commission_rate))),
            min_commission=float(str(data.get("min_commission", cls.min_commission))),
            stamp_duty_sell=float(str(data.get("stamp_duty_sell", cls.stamp_duty_sell))),
            transfer_fee_rate=float(str(data.get("transfer_fee_rate", cls.transfer_fee_rate))),
            price_buffer_buy=float(str(data.get("price_buffer_buy", cls.price_buffer_buy))),
            price_buffer_sell=float(str(data.get("price_buffer_sell", cls.price_buffer_sell))),
            block_limit_up_buy=bool(data.get("block_limit_up_buy", cls.block_limit_up_buy)),
            block_limit_down_sell=bool(data.get("block_limit_down_sell", cls.block_limit_down_sell)),
            max_quote_age_seconds=int(str(data.get("max_quote_age_seconds", cls.max_quote_age_seconds))),
            max_position_age_seconds=int(
                str(data.get("max_position_age_seconds", cls.max_position_age_seconds))
            ),
        )


def _as_bool(value: object) -> bool:
    if pd.isna(value):
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _order_id(trade_date: str, instrument: str, side: str, quantity: int, model_id: str) -> str:
    raw = f"{trade_date}|{instrument}|{side}|{quantity}|{model_id}".encode()
    return hashlib.sha256(raw).hexdigest()[:24]


def _fees(notional: float, side: str, policy: ExecutionPolicy) -> float:
    commission = max(policy.min_commission, notional * policy.commission_rate) if notional > 0 else 0.0
    transfer = notional * policy.transfer_fee_rate
    stamp = notional * policy.stamp_duty_sell if side == "SELL" else 0.0
    return commission + transfer + stamp


def _manifest_risk_policy(manifest: Mapping[str, object]) -> HardRiskPolicy:
    canonical = manifest.get("canonicalConfig")
    if not isinstance(canonical, Mapping):
        raise ArtifactContractError("manifest canonical config is missing")
    risk = canonical.get("risk")
    required = set(HardRiskPolicy.__dataclass_fields__)
    if not isinstance(risk, Mapping) or not required.issubset(risk):
        missing = required - set(risk) if isinstance(risk, Mapping) else required
        raise ArtifactContractError(f"manifest canonical risk config is incomplete: {sorted(missing)}")
    return HardRiskPolicy.from_mapping(risk)


def release_order_intent(
    orders: pd.DataFrame,
    projected_targets: pd.DataFrame,
    *,
    metadata: Mapping[str, str],
    manifest: Mapping[str, object],
    daily_pnl_pct: float | None,
) -> pd.DataFrame:
    """The sole boundary allowed to publish an executable ORDER_INTENT.

    Candidate orders remain ordinary in-memory rows until their projected
    post-trade portfolio passes the release manifest's hard-risk policy.
    """

    if orders.empty:
        return orders
    policy = _manifest_risk_policy(manifest)
    pnl = float("nan") if daily_pnl_pct is None else float(daily_pnl_pct)
    pretrade_risk_check(projected_targets, policy, daily_pnl_pct=pnl)
    if metadata.get("schema_version") == LIVE_ARTIFACT_SCHEMA_VERSION:
        return stamp_live_artifact(
            orders,
            ArtifactType.ORDER_INTENT,
            deployment_id=metadata["deployment_id"],
            dataset_sha256=metadata["dataset_sha256"],
            signal_id=metadata["signal_id"],
            manifest_uri=metadata["manifest_uri"],
            manifest_sha256=metadata["manifest_sha256"],
        )
    return stamp_artifact(
        orders,
        ArtifactType.ORDER_INTENT,
        promotion_status=PromotionStatus.PROMOTED,
        run_id=metadata["run_id"],
        model_id=metadata["model_id"],
        dataset_id=metadata["dataset_id"],
        lineage_id=metadata["lineage_id"],
        manifest_path=metadata["manifest_path"],
    )


def _projected_portfolio(
    orders: pd.DataFrame,
    positions: pd.DataFrame,
    quotes: pd.DataFrame,
    *,
    portfolio_value: float,
    sector_sources: tuple[pd.DataFrame, ...],
) -> pd.DataFrame:
    """Value accepted orders into a post-trade portfolio for hard-risk approval."""

    if portfolio_value <= 0:
        raise ArtifactContractError("projected portfolio value must be positive")
    quantities: dict[str, int] = {}
    for row in positions.to_dict("records"):
        instrument = str(row["instrument"]).upper().strip()
        quantities[instrument] = int(pd.to_numeric(row.get("quantity", 0), errors="coerce"))
    for row in orders.to_dict("records"):
        instrument = str(row["instrument"]).upper().strip()
        quantity = int(pd.to_numeric(row["quantity"], errors="raise"))
        quantities[instrument] = quantities.get(instrument, 0) + (
            quantity if row["side"] == "BUY" else -quantity
        )
        if quantities[instrument] < 0:
            raise ArtifactContractError(f"projected quantity is negative: {instrument}")

    quote = quotes.copy()
    quote["instrument"] = quote["instrument"].astype(str).str.upper().str.strip()
    quote = quote.drop_duplicates("instrument", keep="last").set_index("instrument")
    sectors: dict[str, object] = {}
    for source in sector_sources:
        if "instrument" not in source or "sector" not in source:
            continue
        for row in source[["instrument", "sector"]].dropna(subset=["sector"]).to_dict("records"):
            sectors[str(row["instrument"]).upper().strip()] = row["sector"]

    rows: list[dict[str, object]] = []
    for instrument, quantity in quantities.items():
        if quantity <= 0:
            continue
        if instrument not in quote.index:
            raise ArtifactContractError(f"cannot value projected position without quote: {instrument}")
        price = float(pd.to_numeric(quote.at[instrument, "price"], errors="coerce"))
        if not np.isfinite(price) or price <= 0:
            raise ArtifactContractError(f"cannot value projected position with invalid quote: {instrument}")
        rows.append(
            {
                "instrument": instrument,
                "target_weight": quantity * price / portfolio_value,
                "sector": sectors.get(instrument),
            }
        )
    return pd.DataFrame(rows, columns=["instrument", "target_weight", "sector"])


def _current_market_value(positions: pd.DataFrame, quotes: pd.DataFrame) -> float:
    quote = quotes.copy()
    quote["instrument"] = quote["instrument"].astype(str).str.upper().str.strip()
    quote = quote.drop_duplicates("instrument", keep="last").set_index("instrument")
    value = 0.0
    for row in positions.to_dict("records"):
        quantity = int(pd.to_numeric(row.get("quantity", 0), errors="coerce"))
        if quantity <= 0:
            continue
        instrument = str(row["instrument"]).upper().strip()
        if instrument not in quote.index:
            raise ArtifactContractError(f"cannot value current position without quote: {instrument}")
        price = float(pd.to_numeric(quote.at[instrument, "price"], errors="coerce"))
        if not np.isfinite(price) or price <= 0:
            raise ArtifactContractError(f"cannot value current position with invalid quote: {instrument}")
        value += quantity * price
    return value


def build_orders(
    targets: pd.DataFrame,
    positions: pd.DataFrame,
    quotes: pd.DataFrame,
    *,
    trade_date: str,
    portfolio_value: float,
    cash: float,
    policy: ExecutionPolicy | None = None,
    daily_pnl_pct: float | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Convert target weights to broker-neutral A-share orders.

    `available_quantity` is mandatory for positions and represents T+1 sellable inventory.
    Quotes are point-in-time execution snapshots, not research close prices.
    """

    metadata = validate_artifact(targets, ArtifactType.TARGET_PORTFOLIO)
    model_id = metadata["model_id"]
    manifest = load_artifact_manifest(metadata)
    canonical = manifest.get("canonicalConfig", {})
    configured_execution = canonical.get("execution", {}) if isinstance(canonical, Mapping) else {}
    if not isinstance(configured_execution, Mapping):
        raise ArtifactContractError("manifest canonical execution config is missing")
    governed_policy = ExecutionPolicy.from_mapping(configured_execution)
    if policy is not None and policy != governed_policy:
        raise ArtifactContractError("execution policy does not match the artifact's canonical config")
    policy = governed_policy
    if portfolio_value <= 0:
        raise ValueError("portfolio_value must be positive")
    if cash < 0:
        raise ValueError("cash must be non-negative")
    validate_execution_snapshot(
        positions, name="positions", trade_date=trade_date, max_age_seconds=policy.max_position_age_seconds
    )
    validate_execution_snapshot(
        quotes, name="quotes", trade_date=trade_date, max_age_seconds=policy.max_quote_age_seconds
    )
    required_target = {"instrument", "target_weight"}
    required_position = {"instrument", "quantity", "available_quantity"}
    required_quote = {"instrument", "price", "paused", "is_limit_up", "is_limit_down"}
    for name, frame, required in (
        ("targets", targets, required_target),
        ("positions", positions, required_position),
        ("quotes", quotes, required_quote),
    ):
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"{name} missing columns: {sorted(missing)}")

    t = targets.copy().drop_duplicates("instrument", keep="first").set_index("instrument")
    p = positions.copy().drop_duplicates("instrument", keep="last").set_index("instrument")
    q = quotes.copy().drop_duplicates("instrument", keep="last").set_index("instrument")
    universe = t.index.union(p.index)
    rows: list[dict[str, object]] = []
    blocked: list[dict[str, object]] = []

    for instrument in universe:
        target_weight = (
            float(pd.to_numeric(t.at[instrument, "target_weight"], errors="coerce"))
            if instrument in t.index
            else 0.0
        )
        current_qty = (
            int(pd.to_numeric(p.at[instrument, "quantity"], errors="coerce")) if instrument in p.index else 0
        )
        available_qty = (
            int(pd.to_numeric(p.at[instrument, "available_quantity"], errors="coerce"))
            if instrument in p.index
            else 0
        )
        if instrument not in q.index:
            blocked.append({"instrument": instrument, "reason": "MISSING_QUOTE", "requested_delta": None})
            continue
        quote = q.loc[instrument]
        price = float(pd.to_numeric(quote["price"], errors="coerce"))
        if not np.isfinite(price) or price <= 0:
            blocked.append({"instrument": instrument, "reason": "INVALID_PRICE", "requested_delta": None})
            continue
        lot = int(pd.to_numeric(quote.get("board_lot", policy.board_lot), errors="coerce"))
        lot = lot if lot > 0 else policy.board_lot
        target_qty = int(np.floor((portfolio_value * max(0.0, target_weight)) / price / lot) * lot)
        delta = target_qty - current_qty
        if delta == 0:
            continue
        side = "BUY" if delta > 0 else "SELL"
        requested = abs(delta)
        reasons: list[str] = []
        if _as_bool(quote["paused"]):
            reasons.append("PAUSED")
        if side == "BUY" and policy.block_limit_up_buy and _as_bool(quote["is_limit_up"]):
            reasons.append("LIMIT_UP")
        if side == "SELL" and policy.block_limit_down_sell and _as_bool(quote["is_limit_down"]):
            reasons.append("LIMIT_DOWN")
        if reasons:
            blocked.append({"instrument": instrument, "reason": "+".join(reasons), "requested_delta": delta})
            continue

        quantity = requested
        if side == "SELL":
            quantity = min(quantity, max(0, available_qty))
            if quantity <= 0:
                blocked.append(
                    {"instrument": instrument, "reason": "T1_NOT_SELLABLE", "requested_delta": delta}
                )
                continue
        adv20_volume = pd.to_numeric(quote.get("adv20_volume", np.nan), errors="coerce")
        if np.isfinite(adv20_volume) and adv20_volume > 0:
            participation_cap = int(np.floor(float(adv20_volume) * policy.max_participation_rate / lot) * lot)
            quantity = min(quantity, participation_cap)
        quantity = int(np.floor(quantity / lot) * lot)
        if quantity <= 0:
            blocked.append(
                {"instrument": instrument, "reason": "BELOW_LOT_OR_LIQUIDITY_CAP", "requested_delta": delta}
            )
            continue

        limit_price = (
            price * (1 + policy.price_buffer_buy) if side == "BUY" else price * (1 - policy.price_buffer_sell)
        )
        notional = quantity * limit_price
        fee = _fees(notional, side, policy)
        score = float(t.at[instrument, "score"]) if instrument in t.index and "score" in t.columns else np.nan
        rows.append(
            {
                "trade_date": trade_date,
                "instrument": instrument,
                "side": side,
                "quantity": quantity,
                "limit_price": round(limit_price, 4),
                "estimated_notional": round(notional, 2),
                "estimated_fees": round(fee, 2),
                "target_weight": target_weight,
                "current_quantity": current_qty,
                "available_quantity": available_qty,
                "score": score,
                "client_order_id": _order_id(trade_date, str(instrument), side, quantity, model_id),
                "status": "READY",
            }
        )

    orders = pd.DataFrame(rows)
    blocked_df = pd.DataFrame(blocked)
    if orders.empty:
        return orders, blocked_df

    # Process sells first; proceeds are conservatively reduced by fees. Buys are then cash-gated by score.
    sell_mask = orders["side"] == "SELL"
    available_cash = float(cash) + float(
        (orders.loc[sell_mask, "estimated_notional"] - orders.loc[sell_mask, "estimated_fees"]).sum()
    )
    buys = orders.loc[~sell_mask].sort_values(["score", "instrument"], ascending=[False, True])
    accepted_buy_indices: list[int] = []
    for idx, row in buys.iterrows():
        total = float(row["estimated_notional"] + row["estimated_fees"])
        if total <= available_cash + 1e-9:
            accepted_buy_indices.append(idx)
            available_cash -= total
        else:
            blocked_df = pd.concat(
                [
                    blocked_df,
                    pd.DataFrame(
                        [
                            {
                                "instrument": row["instrument"],
                                "reason": "INSUFFICIENT_CASH",
                                "requested_delta": row["quantity"],
                            }
                        ]
                    ),
                ],
                ignore_index=True,
            )
    orders = pd.concat([orders.loc[sell_mask], orders.loc[accepted_buy_indices]], ignore_index=True)
    orders = orders.sort_values(["side", "score", "instrument"], ascending=[False, False, True]).reset_index(
        drop=True
    )
    projected = _projected_portfolio(
        orders,
        positions,
        quotes,
        # Risk uses broker cash plus freshly marked holdings rather than the
        # caller-provided sizing NAV, which must never dilute exposure checks.
        portfolio_value=float(cash) + _current_market_value(positions, quotes),
        sector_sources=(targets, positions, quotes),
    )
    orders = release_order_intent(
        orders,
        projected,
        metadata=metadata,
        manifest=manifest,
        daily_pnl_pct=daily_pnl_pct,
    )
    return orders, blocked_df.reset_index(drop=True)


def _topk_quote_frame(quotes: pd.DataFrame) -> pd.DataFrame:
    required = {"instrument", "price", "paused", "is_limit_up", "is_limit_down"}
    missing = required - set(quotes.columns)
    if missing:
        raise ValueError(f"quotes missing columns: {sorted(missing)}")
    frame = quotes.copy()
    frame["instrument"] = frame["instrument"].astype(str).str.upper().str.strip()
    return frame.drop_duplicates("instrument", keep="last").set_index("instrument")


def _topk_positions(positions: pd.DataFrame) -> pd.DataFrame:
    frame = positions.copy()
    required = {"instrument", "quantity", "available_quantity", "holding_days"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"positions missing columns: {sorted(missing)}")
    return frame


def _topk_quantity_cap(quote: pd.Series, quantity: float, lot: int, policy: ExecutionPolicy) -> int:
    capped = max(0.0, quantity)
    adv20_volume = pd.to_numeric(quote.get("adv20_volume", np.nan), errors="coerce")
    if np.isfinite(adv20_volume) and adv20_volume > 0:
        capped = min(capped, float(adv20_volume) * policy.max_participation_rate)
    return int(np.floor(capped / lot) * lot)


def build_topk_orders(
    scores: pd.Series | pd.DataFrame,
    positions: pd.DataFrame,
    quotes: pd.DataFrame,
    *,
    signal_date: str,
    trade_date: str,
    cash: float,
    strategy_policy: TopkDropoutPolicy | None = None,
    execution_policy: ExecutionPolicy | None = None,
    daily_pnl_pct: float | None = None,
    artifact_resolver: ArtifactResolver | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Generate Qlib-style TopkDropout intents and broker-neutral orders.

    The strategy uses the complete score cross-section and broker-reconciled
    ``holding_days``.  The order phase deliberately uses ADV20 as the live
    liquidity cap because the current-day volume used by a Qlib backtest is not
    available before the market opens.
    """

    if not isinstance(scores, pd.DataFrame):
        raise ArtifactContractError("build_topk_orders requires a versioned MODEL_SCORE DataFrame")
    schema_values = scores.get("schema_version", pd.Series(dtype=str)).dropna().astype(str).unique()
    is_live = len(schema_values) == 1 and schema_values[0] == LIVE_ARTIFACT_SCHEMA_VERSION
    if is_live:
        if artifact_resolver is None:
            raise ArtifactContractError("schema 3.0 MODEL_SCORE requires an ArtifactResolver")
        metadata = validate_live_artifact(
            scores, ArtifactType.MODEL_SCORE, resolver=artifact_resolver
        )
        attestation_path = artifact_resolver.resolve(
            metadata["manifest_uri"], expected_sha256=metadata["manifest_sha256"]
        )
        manifest = json.loads(attestation_path.read_text(encoding="utf-8"))
        metadata = {
            **metadata,
            "run_id": metadata["signal_id"],
            "model_id": metadata["deployment_id"],
            "dataset_id": metadata["dataset_sha256"],
            "lineage_id": metadata["signal_id"],
            "manifest_path": str(attestation_path),
        }
    else:
        metadata = validate_artifact(scores, ArtifactType.MODEL_SCORE)
        manifest = load_artifact_manifest(metadata)
    required_score = {"instrument", "score", "signal_date", "trade_date"}
    if not required_score.issubset(scores.columns):
        raise ArtifactContractError(
            f"MODEL_SCORE artifact missing columns: {sorted(required_score - set(scores.columns))}"
        )
    signal_values = pd.to_datetime(scores["signal_date"], errors="coerce").dt.normalize()
    trade_values = pd.to_datetime(scores["trade_date"], errors="coerce").dt.normalize()
    if (
        signal_values.isna().any()
        or trade_values.isna().any()
        or signal_values.nunique() != 1
        or trade_values.nunique() != 1
        or signal_values.iloc[0] != pd.Timestamp(signal_date).normalize()
        or trade_values.iloc[0] != pd.Timestamp(trade_date).normalize()
    ):
        raise ArtifactContractError(
            "MODEL_SCORE signal_date/trade_date does not match this execution request"
        )
    score_values = scores.set_index("instrument")["score"]
    model_id = metadata["model_id"]
    canonical = manifest.get("canonicalConfig", {})
    configured_strategy = canonical.get("strategy", {}) if isinstance(canonical, Mapping) else {}
    configured_execution = canonical.get("execution", {}) if isinstance(canonical, Mapping) else {}
    if not isinstance(configured_strategy, Mapping) or not isinstance(configured_execution, Mapping):
        raise ArtifactContractError("manifest canonical strategy/execution config is missing")
    governed_strategy = TopkDropoutPolicy.from_mapping(configured_strategy)
    governed_execution = ExecutionPolicy.from_mapping(configured_execution)
    if strategy_policy is not None and strategy_policy != governed_strategy:
        raise ArtifactContractError("strategy policy does not match the artifact's canonical config")
    if execution_policy is not None and execution_policy != governed_execution:
        raise ArtifactContractError("execution policy does not match the artifact's canonical config")
    strategy_policy = governed_strategy
    execution_policy = governed_execution
    if cash < 0:
        raise ValueError("cash must be non-negative")
    validate_execution_snapshot(
        positions,
        name="positions",
        trade_date=trade_date,
        max_age_seconds=execution_policy.max_position_age_seconds,
    )
    validate_execution_snapshot(
        quotes,
        name="quotes",
        trade_date=trade_date,
        max_age_seconds=execution_policy.max_quote_age_seconds,
    )
    current = _topk_positions(positions)
    quote = _topk_quote_frame(quotes)
    decision = topk_dropout_decision(
        score_values,
        current,
        quote.reset_index(),
        policy=strategy_policy,
        signal_date=signal_date,
        trade_date=trade_date,
    )
    position_index = current.copy()
    position_index["instrument"] = position_index["instrument"].astype(str).str.upper().str.strip()
    position_index = position_index.drop_duplicates("instrument", keep="last").set_index("instrument")
    rows: list[dict[str, object]] = []
    blocked: list[dict[str, object]] = []

    def append_order(
        row: pd.Series,
        side: str,
        quantity: int,
        current_quantity: int,
        available_quantity: int,
    ) -> tuple[float, float]:
        instrument = str(row["instrument"])
        quote_row = quote.loc[instrument]
        price = float(pd.to_numeric(quote_row["price"], errors="coerce"))
        notional = quantity * price
        fee = _fees(notional, side, execution_policy)
        rows.append(
            {
                "trade_date": pd.Timestamp(trade_date).strftime("%Y-%m-%d"),
                "signal_date": pd.Timestamp(signal_date).strftime("%Y-%m-%d"),
                "instrument": instrument,
                "side": side,
                "quantity": quantity,
                "limit_price": round(price, 4),
                "estimated_notional": round(notional, 2),
                "estimated_fees": round(fee, 2),
                "score": float(row["score"]),
                "score_rank": row["score_rank"],
                "current_quantity": current_quantity,
                "available_quantity": available_quantity,
                "target_action": row["target_action"],
                "action_reason": row["action_reason"],
                "action_order": row["action_order"],
                "client_order_id": _order_id(str(trade_date), instrument, side, quantity, model_id),
                "status": "READY",
            }
        )
        return notional, fee

    sells = decision.loc[decision["target_action"] == "SELL"].sort_values("action_order")
    sale_proceeds = 0.0
    for strategy_row in sells.to_dict("records"):
        row = pd.Series(strategy_row)
        instrument = str(row["instrument"])
        if instrument not in quote.index:
            blocked.append({"instrument": instrument, "reason": "MISSING_QUOTE", "requested_delta": None})
            continue
        quote_row = quote.loc[instrument]
        price = float(pd.to_numeric(quote_row["price"], errors="coerce"))
        if not np.isfinite(price) or price <= 0:
            blocked.append({"instrument": instrument, "reason": "INVALID_PRICE", "requested_delta": None})
            continue
        current_quantity = int(pd.to_numeric(position_index.at[instrument, "quantity"], errors="coerce"))
        available_quantity = int(
            pd.to_numeric(position_index.at[instrument, "available_quantity"], errors="coerce")
        )
        lot = int(pd.to_numeric(quote_row.get("board_lot", execution_policy.board_lot), errors="coerce"))
        lot = lot if lot > 0 else execution_policy.board_lot
        quantity = _topk_quantity_cap(
            quote_row, min(current_quantity, available_quantity), lot, execution_policy
        )
        if quantity <= 0:
            reason = "T1_NOT_SELLABLE" if available_quantity <= 0 else "BELOW_LOT_OR_LIQUIDITY_CAP"
            blocked.append({"instrument": instrument, "reason": reason, "requested_delta": -current_quantity})
            continue
        notional, fee = append_order(row, "SELL", quantity, current_quantity, available_quantity)
        sale_proceeds += notional - fee

    buys = decision.loc[decision["target_action"] == "BUY"].sort_values("action_order")
    available_cash = float(cash) + sale_proceeds
    per_buy_budget = available_cash * strategy_policy.risk_degree / len(buys) if len(buys) else 0.0
    for strategy_row in buys.to_dict("records"):
        row = pd.Series(strategy_row)
        instrument = str(row["instrument"])
        if instrument not in quote.index:
            blocked.append({"instrument": instrument, "reason": "MISSING_QUOTE", "requested_delta": None})
            continue
        quote_row = quote.loc[instrument]
        price = float(pd.to_numeric(quote_row["price"], errors="coerce"))
        if not np.isfinite(price) or price <= 0:
            blocked.append({"instrument": instrument, "reason": "INVALID_PRICE", "requested_delta": None})
            continue
        lot = int(pd.to_numeric(quote_row.get("board_lot", execution_policy.board_lot), errors="coerce"))
        lot = lot if lot > 0 else execution_policy.board_lot
        quantity = _topk_quantity_cap(quote_row, per_buy_budget / price, lot, execution_policy)
        if quantity <= 0:
            blocked.append(
                {"instrument": instrument, "reason": "BELOW_LOT_OR_LIQUIDITY_CAP", "requested_delta": None}
            )
            continue
        notional = quantity * price
        fee = _fees(notional, "BUY", execution_policy)
        if notional + fee > available_cash + 1e-9:
            blocked.append(
                {"instrument": instrument, "reason": "INSUFFICIENT_CASH", "requested_delta": quantity}
            )
            continue
        current_quantity = (
            int(pd.to_numeric(position_index.at[instrument, "quantity"], errors="coerce"))
            if instrument in position_index.index
            else 0
        )
        available_quantity = (
            int(pd.to_numeric(position_index.at[instrument, "available_quantity"], errors="coerce"))
            if instrument in position_index.index
            else 0
        )
        append_order(row, "BUY", quantity, current_quantity, available_quantity)
        available_cash -= notional + fee

    orders = pd.DataFrame(rows)
    if not orders.empty:
        orders["_side_order"] = orders["side"].map({"SELL": 0, "BUY": 1})
        orders = orders.sort_values(["_side_order", "action_order", "score_rank", "instrument"])
        orders = orders.drop(columns=["_side_order", "action_order"], errors="ignore").reset_index(drop=True)
        current_market_value = _current_market_value(current, quotes)
        projected = _projected_portfolio(
            orders,
            current,
            quotes,
            portfolio_value=float(cash) + current_market_value,
            sector_sources=(scores, current, quotes),
        )
        orders = release_order_intent(
            orders,
            projected,
            metadata=metadata,
            manifest=manifest,
            daily_pnl_pct=daily_pnl_pct,
        )
    if not decision.empty and is_live:
        decision = stamp_live_artifact(
            decision,
            ArtifactType.STRATEGY_DECISION,
            deployment_id=metadata["deployment_id"],
            dataset_sha256=metadata["dataset_sha256"],
            signal_id=metadata["signal_id"],
            manifest_uri=metadata["manifest_uri"],
            manifest_sha256=metadata["manifest_sha256"],
        )
    elif not decision.empty:
        decision = stamp_artifact(
            decision,
            ArtifactType.STRATEGY_DECISION,
            promotion_status=PromotionStatus.PROMOTED,
            run_id=metadata["run_id"],
            model_id=model_id,
            dataset_id=metadata["dataset_id"],
            lineage_id=metadata["lineage_id"],
            manifest_path=metadata["manifest_path"],
        )
    return decision, orders, pd.DataFrame(blocked)
