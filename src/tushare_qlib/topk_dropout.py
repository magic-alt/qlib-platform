from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class TopkDropoutPolicy:
    """The long-only decision parameters used by Qlib's TopkDropoutStrategy.

    The implementation deliberately keeps the Qlib semantics where ``topk`` is
    a target size rather than a hard position limit.  In particular, blocked
    sells caused by ``hold_thresh`` do not remove the corresponding buy intent.
    """

    topk: int = 30
    n_drop: int = 5
    hold_thresh: int = 5
    only_tradable: bool = True
    forbid_all_trade_at_limit: bool = True
    risk_degree: float = 0.95

    @classmethod
    def from_mapping(cls, data: Mapping[str, object] | None) -> "TopkDropoutPolicy":
        data = data or {}
        return cls(
            topk=int(data.get("topk", cls.topk)),
            n_drop=int(data.get("n_drop", cls.n_drop)),
            hold_thresh=int(data.get("hold_thresh", cls.hold_thresh)),
            only_tradable=bool(data.get("only_tradable", cls.only_tradable)),
            forbid_all_trade_at_limit=bool(data.get("forbid_all_trade_at_limit", cls.forbid_all_trade_at_limit)),
            risk_degree=float(data.get("risk_degree", cls.risk_degree)),
        )

    def validate(self) -> None:
        if self.topk <= 0:
            raise ValueError("topk must be positive")
        if self.n_drop <= 0:
            raise ValueError("n_drop must be positive")
        if self.hold_thresh < 0:
            raise ValueError("hold_thresh must be non-negative")
        if not 0 < self.risk_degree <= 1:
            raise ValueError("risk_degree must be in (0, 1]")


def _normalise_scores(scores: pd.Series | pd.DataFrame) -> pd.Series:
    if isinstance(scores, pd.DataFrame):
        if scores.shape[1] != 1:
            raise ValueError("scores must contain exactly one score column")
        scores = scores.iloc[:, 0]
    result = pd.to_numeric(scores.copy(), errors="coerce")
    result.index = result.index.astype(str).str.upper().str.strip()
    result = result.loc[~result.index.duplicated(keep="first")].dropna()
    if result.empty:
        raise ValueError("scores are empty")
    return result


def _normalise_positions(positions: pd.DataFrame | None) -> pd.DataFrame:
    columns = ["instrument", "quantity", "available_quantity", "holding_days"]
    if positions is None or positions.empty:
        return pd.DataFrame(columns=columns)
    if "instrument" not in positions.columns or "quantity" not in positions.columns:
        raise ValueError("positions must contain instrument and quantity")
    frame = positions.copy()
    frame["instrument"] = frame["instrument"].astype(str).str.upper().str.strip()
    frame["quantity"] = pd.to_numeric(frame["quantity"], errors="coerce").fillna(0.0)
    frame = frame.loc[frame["quantity"] > 0].drop_duplicates("instrument", keep="last")
    available = frame["available_quantity"] if "available_quantity" in frame else frame["quantity"]
    holding = frame["holding_days"] if "holding_days" in frame else pd.Series(0, index=frame.index)
    frame["available_quantity"] = pd.to_numeric(available, errors="coerce").fillna(0.0)
    frame["holding_days"] = pd.to_numeric(holding, errors="coerce").fillna(0).astype(int)
    return frame[columns].reset_index(drop=True)


def _normalise_quotes(quotes: pd.DataFrame | None, *, required: bool) -> pd.DataFrame:
    columns = ["instrument", "paused", "is_limit_up", "is_limit_down"]
    if quotes is None:
        if required:
            raise ValueError("quotes are required when only_tradable is enabled")
        return pd.DataFrame(columns=columns)
    if quotes.empty:
        if required:
            raise ValueError("quotes are empty when only_tradable is enabled")
        return pd.DataFrame(columns=columns)
    missing = {"instrument", "paused", "is_limit_up", "is_limit_down"} - set(quotes.columns)
    if missing:
        raise ValueError(f"quotes missing columns: {sorted(missing)}")
    frame = quotes.copy()
    frame["instrument"] = frame["instrument"].astype(str).str.upper().str.strip()
    for column in columns[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(1.0).astype(float)
    return frame[columns].drop_duplicates("instrument", keep="last").set_index("instrument")


def _quote_flags(instruments: pd.Index, quotes: pd.DataFrame, policy: TopkDropoutPolicy) -> pd.DataFrame:
    if quotes.empty:
        flags = pd.DataFrame(index=instruments)
        flags["base_tradable"] = True
        flags["candidate_tradable"] = True
        flags["buy_tradable"] = True
        flags["sell_tradable"] = True
        return flags
    frame = quotes.reindex(instruments)
    base = frame["paused"].eq(0.0)
    buy_limited = frame["is_limit_up"].eq(1.0)
    sell_limited = frame["is_limit_down"].eq(1.0)
    flags = pd.DataFrame(index=instruments)
    flags["base_tradable"] = base.fillna(False)
    # Qlib filters ``today`` using direction=None, so either price limit rules
    # a name out of the candidate list even when directional trading is allowed.
    flags["candidate_tradable"] = (base & ~buy_limited & ~sell_limited).fillna(False)
    if policy.forbid_all_trade_at_limit:
        flags["buy_tradable"] = flags["candidate_tradable"]
        flags["sell_tradable"] = flags["candidate_tradable"]
    else:
        flags["buy_tradable"] = (base & ~buy_limited).fillna(False)
        flags["sell_tradable"] = (base & ~sell_limited).fillna(False)
    return flags


def _first_n(values: pd.Index, n: int, tradable: pd.Series | None) -> pd.Index:
    """Match Qlib's local ``get_first_n`` helper, including its n<=0 edge case."""

    if tradable is None:
        return values[:n]
    selected: list[str] = []
    for instrument in values:
        if bool(tradable.get(instrument, False)):
            selected.append(str(instrument))
            if len(selected) >= n:
                break
    return pd.Index(selected)


def _last_n(values: pd.Index, n: int, tradable: pd.Series | None) -> pd.Index:
    if tradable is None:
        return values[-n:]
    return _first_n(values[::-1], n, tradable)[::-1]


def topk_dropout_decision(
    scores: pd.Series | pd.DataFrame,
    positions: pd.DataFrame | None,
    quotes: pd.DataFrame | None = None,
    *,
    policy: TopkDropoutPolicy | None = None,
    signal_date: str | pd.Timestamp | None = None,
    trade_date: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Generate an explainable, Qlib-compatible TopkDropout decision table.

    The returned rows are the union of model TopK, current holdings, buy
    candidates and sell candidates.  A ``target_action`` is an actionable
    strategy request; execution constraints such as T+1 inventory and ADV20
    capacity are applied later by :func:`tushare_qlib.execution.build_topk_orders`.
    """

    policy = policy or TopkDropoutPolicy()
    policy.validate()
    score = _normalise_scores(scores)
    current = _normalise_positions(positions)
    quote = _normalise_quotes(quotes, required=policy.only_tradable)

    ranked = score.sort_values(ascending=False)
    score_rank = pd.Series(np.arange(1, len(ranked) + 1, dtype=int), index=ranked.index)
    current_list = pd.Index(current["instrument"].tolist())
    all_instruments = score.index.union(current_list)
    flags = _quote_flags(all_instruments, quote, policy)

    # This mirrors TopkDropoutStrategy: holdings are first ranked by today's
    # signal, then the best not-held names are considered as potential entrants.
    last = score.reindex(current_list).sort_values(ascending=False).index
    unseen = ranked.loc[~ranked.index.isin(last)].index
    candidate_count = policy.n_drop + policy.topk - len(last)
    candidate_filter = flags["candidate_tradable"] if policy.only_tradable else None
    today = _first_n(unseen, candidate_count, candidate_filter)
    comb = score.reindex(last.union(today)).sort_values(ascending=False).index
    sell_filter = flags["candidate_tradable"] if policy.only_tradable else None
    theoretical_sell = last[last.isin(_last_n(comb, policy.n_drop, sell_filter))]
    buy = today[: len(theoretical_sell) + policy.topk - len(last)]

    holding_days = current.set_index("instrument")["holding_days"] if not current.empty else pd.Series(dtype=int)
    relevant = pd.Index(last).union(today).union(ranked.head(policy.topk).index).union(theoretical_sell).union(buy)
    rows: list[dict[str, object]] = []
    sell_order = {str(instrument): idx for idx, instrument in enumerate(theoretical_sell)}
    buy_order = {str(instrument): idx for idx, instrument in enumerate(buy)}
    current_set = set(current_list)
    sell_set = set(theoretical_sell)
    buy_set = set(buy)
    topk_set = set(ranked.head(policy.topk).index)

    for instrument in relevant:
        instrument = str(instrument)
        is_current = instrument in current_set
        is_sell = instrument in sell_set
        is_buy = instrument in buy_set
        days = int(holding_days.get(instrument, 0))
        if is_sell and days < policy.hold_thresh:
            action, reason = "HOLD", "HOLD_THRESHOLD"
        elif is_sell and not bool(flags.at[instrument, "sell_tradable"]):
            action, reason = "HOLD", "NOT_TRADABLE_SELL"
        elif is_sell:
            action, reason = "SELL", "DROP_LOWEST_COMBINED_SCORE"
        elif is_buy and not bool(flags.at[instrument, "buy_tradable"]):
            action, reason = "HOLD", "NOT_TRADABLE_BUY"
        elif is_buy:
            action, reason = "BUY", "TOPK_FILL_OR_REPLACEMENT"
        elif is_current:
            action, reason = "HOLD", "CURRENT_POSITION"
        elif instrument in topk_set:
            action, reason = "HOLD", "MODEL_TOPK_NOT_NEEDED"
        else:
            action, reason = "HOLD", "CANDIDATE_NOT_SELECTED"
        rows.append(
            {
                "signal_date": pd.Timestamp(signal_date).strftime("%Y-%m-%d") if signal_date is not None else None,
                "trade_date": pd.Timestamp(trade_date).strftime("%Y-%m-%d") if trade_date is not None else None,
                "instrument": instrument,
                "score": float(score.get(instrument, np.nan)),
                "score_rank": int(score_rank[instrument]) if instrument in score_rank else pd.NA,
                "is_model_topk": instrument in topk_set,
                "is_current_position": is_current,
                "holding_days": days if is_current else pd.NA,
                "candidate_tradable": bool(flags.at[instrument, "candidate_tradable"]),
                "buy_tradable": bool(flags.at[instrument, "buy_tradable"]),
                "sell_tradable": bool(flags.at[instrument, "sell_tradable"]),
                "is_buy_candidate": instrument in set(today),
                "is_sell_candidate": is_sell,
                "target_action": action,
                "action_reason": reason,
                "action_order": sell_order.get(instrument, buy_order.get(instrument, pd.NA)),
            }
        )
    result = pd.DataFrame(rows)
    if result.empty:
        return result
    action_rank = result["target_action"].map({"SELL": 0, "BUY": 1, "HOLD": 2}).fillna(3)
    return (
        result.assign(_action_rank=action_rank)
        .sort_values(["_action_rank", "action_order", "score_rank", "instrument"], na_position="last")
        .drop(columns="_action_rank")
        .reset_index(drop=True)
    )
