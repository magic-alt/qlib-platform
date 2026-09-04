from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd

from .failure_attribution import FailureAttributionSpec


REQUIRED_PORTFOLIO_COLUMNS = {"return", "bench", "cost"}


def _normalize_report(report: pd.DataFrame) -> pd.DataFrame:
    frame = report.copy()
    if not isinstance(frame.index, pd.DatetimeIndex):
        if "trade_date" not in frame:
            raise ValueError("portfolio report requires a DatetimeIndex or trade_date column")
        frame = frame.set_index("trade_date")
    frame.index = pd.DatetimeIndex(pd.to_datetime(frame.index, errors="raise")).normalize()
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError("portfolio report dates must be unique and increasing")
    missing = REQUIRED_PORTFOLIO_COLUMNS - set(frame)
    if missing:
        raise ValueError(f"portfolio report is missing columns: {sorted(missing)}")
    for column in REQUIRED_PORTFOLIO_COLUMNS | {"turnover", "account", "total_cost", "total_turnover"}:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame[list(REQUIRED_PORTFOLIO_COLUMNS)].isna().any().any():
        raise ValueError("portfolio report contains non-numeric return, benchmark, or cost values")
    return frame.sort_index()


def _prediction_dates(predictions: pd.DataFrame) -> pd.DatetimeIndex:
    if not isinstance(predictions.index, pd.MultiIndex) or "datetime" not in predictions.index.names:
        raise ValueError("portfolio attribution predictions require a datetime MultiIndex")
    return pd.DatetimeIndex(predictions.index.get_level_values("datetime").unique()).normalize().sort_values()


def _audit_signal_dates(audit: pd.DataFrame) -> dict[pd.Timestamp, pd.Timestamp]:
    if audit.empty or not {"trade_date", "signal_date"}.issubset(audit):
        return {}
    frame = audit[["trade_date", "signal_date"]].dropna().copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise").dt.normalize()
    frame["signal_date"] = pd.to_datetime(frame["signal_date"], errors="raise").dt.normalize()
    counts = frame.groupby("trade_date")["signal_date"].nunique()
    if counts.gt(1).any():
        raise ValueError("strategy audit maps one trade date to multiple signal dates")
    unique = frame.drop_duplicates("trade_date")
    return {
        pd.Timestamp(row.trade_date).normalize(): pd.Timestamp(row.signal_date).normalize()
        for row in unique.itertuples(index=False)
    }


def _asof_signal_date(
    trade_date: pd.Timestamp,
    prediction_dates: pd.DatetimeIndex,
    audit_mapping: Mapping[pd.Timestamp, pd.Timestamp],
) -> pd.Timestamp | pd.NaT:
    normalized = pd.Timestamp(trade_date).normalize()
    if normalized in audit_mapping:
        return pd.Timestamp(audit_mapping[normalized]).normalize()
    position = prediction_dates.searchsorted(normalized, side="left") - 1
    if position < 0:
        return pd.NaT
    return pd.Timestamp(prediction_dates[position]).normalize()


def build_daily_portfolio_bridge(
    report: pd.DataFrame,
    predictions: pd.DataFrame,
    audit: pd.DataFrame,
    *,
    fold_assignments: Mapping[pd.Timestamp, str],
) -> pd.DataFrame:
    frame = _normalize_report(report)
    prediction_dates = _prediction_dates(predictions)
    audit_mapping = _audit_signal_dates(audit)
    result = frame.reset_index(names="trade_date")
    result["signal_date"] = result["trade_date"].map(
        lambda value: _asof_signal_date(pd.Timestamp(value), prediction_dates, audit_mapping)
    )
    result["fold"] = result["signal_date"].map(fold_assignments)
    result["gross_return"] = result["return"]
    result["benchmark_return"] = result["bench"]
    result["explicit_cost"] = result["cost"]
    result["gross_excess"] = result["gross_return"] - result["benchmark_return"]
    result["net_return"] = result["gross_return"] - result["explicit_cost"]
    result["net_excess"] = result["gross_excess"] - result["explicit_cost"]
    attributed = result["signal_date"].notna()
    if result.loc[attributed, "fold"].isna().any():
        raise ValueError("portfolio dates are absent from certified rolling fold assignments")
    return result


def build_daily_holdings_conversion(
    holdings: pd.DataFrame,
    predictions: pd.DataFrame,
    audit: pd.DataFrame,
    *,
    topk: int,
    fold_assignments: Mapping[pd.Timestamp, str],
) -> pd.DataFrame:
    required = {"trade_date", "instrument"}
    if holdings.empty:
        return pd.DataFrame(
            columns=[
                "trade_date",
                "signal_date",
                "fold",
                "holding_count",
                "gross_exposure",
                "realized_topk_overlap",
                "holdings_turnover",
                "mean_holding_days",
            ]
        )
    if not required.issubset(holdings):
        raise ValueError(f"holdings are missing columns: {sorted(required - set(holdings))}")
    frame = holdings.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise").dt.normalize()
    frame["instrument"] = frame["instrument"].astype(str)
    if frame.duplicated(["trade_date", "instrument"]).any():
        raise ValueError("holdings contain duplicate trade_date/instrument keys")
    prediction_dates = _prediction_dates(predictions)
    audit_mapping = _audit_signal_dates(audit)
    scores = pd.to_numeric(predictions["score"], errors="coerce")
    previous: set[str] | None = None
    rows: list[dict[str, object]] = []
    for trade_date, block in frame.groupby("trade_date", sort=True):
        signal_date = _asof_signal_date(pd.Timestamp(trade_date), prediction_dates, audit_mapping)
        if pd.isna(signal_date):
            continue
        daily_scores = (
            scores.xs(signal_date, level="datetime").dropna().sort_values(ascending=False, kind="stable")
        )
        target = set(daily_scores.head(topk).index.astype(str))
        held = set(block["instrument"])
        denominator = max(1, min(topk, len(target)))
        overlap = len(target & held) / denominator
        turnover = float("nan")
        if previous is not None:
            turnover = 1.0 - len(previous & held) / max(1, len(previous))
        gross_exposure = (
            float(pd.to_numeric(block["weight"], errors="coerce").abs().sum())
            if "weight" in block
            else float("nan")
        )
        mean_holding_days = (
            float(pd.to_numeric(block["holding_days"], errors="coerce").mean())
            if "holding_days" in block
            else float("nan")
        )
        normalized_signal = pd.Timestamp(signal_date).normalize()
        rows.append(
            {
                "trade_date": pd.Timestamp(trade_date).normalize(),
                "signal_date": normalized_signal,
                "fold": fold_assignments.get(normalized_signal),
                "holding_count": len(held),
                "gross_exposure": gross_exposure,
                "realized_topk_overlap": overlap,
                "holdings_turnover": turnover,
                "mean_holding_days": mean_holding_days,
            }
        )
        previous = held
    result = pd.DataFrame(rows)
    if not result.empty and result["fold"].isna().any():
        raise ValueError("holding dates are absent from certified rolling fold assignments")
    return result


def _compound(values: pd.Series) -> float:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    return float((1.0 + numeric).prod() - 1.0) if len(numeric) else float("nan")


def _annualized_ir(values: pd.Series, annualization_sessions: int) -> float:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    std = float(numeric.std(ddof=1))
    if len(numeric) < 2 or not np.isfinite(std) or std <= 0:
        return float("nan")
    return float(numeric.mean() / std * np.sqrt(annualization_sessions))


def _annualized_vol(values: pd.Series, annualization_sessions: int) -> float:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    std = float(numeric.std(ddof=1))
    if len(numeric) < 2 or not np.isfinite(std) or std <= 0:
        return float("nan")
    return float(std * np.sqrt(annualization_sessions))


def _max_drawdown(values: pd.Series) -> float:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if numeric.empty:
        return float("nan")
    equity = pd.concat([pd.Series([1.0]), (1.0 + numeric).cumprod().reset_index(drop=True)])
    return float((equity / equity.cummax() - 1.0).min())


def _scope_dates(
    daily: pd.DataFrame,
    regime_labels: pd.DataFrame,
) -> list[tuple[str, str, str | None, str | None, pd.DataFrame]]:
    scopes: list[tuple[str, str, str | None, str | None, pd.DataFrame]] = [
        ("ALL_OOS", "ALL_OOS", None, None, daily)
    ]
    for fold, block in daily.loc[daily["fold"].notna()].groupby("fold", sort=True):
        scopes.append(("FOLD", str(fold), None, None, block))
    regimes = regime_labels.loc[
        regime_labels["status"].eq("AVAILABLE"), ["date", "dimension", "state"]
    ].copy()
    regimes["signal_date"] = pd.to_datetime(regimes.pop("date"), errors="raise").dt.normalize()
    merged = daily.loc[daily["signal_date"].notna()].merge(
        regimes, on="signal_date", how="inner", validate="many_to_many"
    )
    for (dimension, state), block in merged.groupby(["dimension", "state"], sort=True):
        scopes.append(("REGIME", f"{dimension}:{state}", str(dimension), str(state), block))
    return scopes


def summarize_portfolio_bridge(
    daily: pd.DataFrame,
    holdings_daily: pd.DataFrame,
    regime_labels: pd.DataFrame,
    *,
    run_name: str,
    model: str,
    variant: str,
    spec: FailureAttributionSpec,
) -> pd.DataFrame:
    holding_lookup = holdings_daily.set_index("signal_date") if not holdings_daily.empty else None
    rows: list[dict[str, object]] = []
    for scope_type, scope, dimension, state, block in _scope_dates(daily, regime_labels):
        signal_dates = pd.DatetimeIndex(block["signal_date"].dropna().unique())
        scoped_holdings = (
            holding_lookup.loc[holding_lookup.index.intersection(signal_dates)]
            if holding_lookup is not None
            else pd.DataFrame()
        )
        gross_return = _compound(block["gross_return"])
        benchmark_return = _compound(block["benchmark_return"])
        net_return = _compound(block["net_return"])
        cost_return = gross_return - net_return
        turnover = pd.to_numeric(block.get("turnover"), errors="coerce") if "turnover" in block else None
        rows.append(
            {
                "run": run_name,
                "model": model,
                "variant": variant,
                "scope_type": scope_type,
                "scope": scope,
                "dimension": dimension,
                "state": state,
                "sessions": int(block["trade_date"].nunique()),
                "attributed_sessions": int(block["signal_date"].nunique()),
                "gross_return": gross_return,
                "benchmark_return": benchmark_return,
                "gross_excess_return": gross_return - benchmark_return,
                "explicit_transaction_cost": float(block["explicit_cost"].sum()),
                "cost_return": cost_return,
                "net_return": net_return,
                "net_excess_return": net_return - benchmark_return,
                "excess_ir": _annualized_ir(block["net_excess"], spec.annualization_sessions),
                "max_drawdown": _max_drawdown(block["net_return"]),
                "annual_turnover": (
                    float(turnover.mean() * spec.annualization_sessions)
                    if turnover is not None and turnover.notna().any()
                    else float("nan")
                ),
                "mean_holding_count": (
                    float(scoped_holdings["holding_count"].mean())
                    if not scoped_holdings.empty
                    else float("nan")
                ),
                "mean_gross_exposure": (
                    float(scoped_holdings["gross_exposure"].mean())
                    if not scoped_holdings.empty
                    else float("nan")
                ),
                "realized_topk_overlap": (
                    float(scoped_holdings["realized_topk_overlap"].mean())
                    if not scoped_holdings.empty
                    else float("nan")
                ),
                "realized_holdings_turnover": (
                    float(scoped_holdings["holdings_turnover"].mean())
                    if not scoped_holdings.empty
                    else float("nan")
                ),
                "mean_holding_days": (
                    float(scoped_holdings["mean_holding_days"].mean())
                    if not scoped_holdings.empty
                    else float("nan")
                ),
            }
        )
    return pd.DataFrame(rows)


def derive_cost_sensitivity(
    daily: pd.DataFrame,
    regime_labels: pd.DataFrame,
    *,
    run_name: str,
    model: str,
    variant: str,
    spec: FailureAttributionSpec,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for scope_type, scope, dimension, state, block in _scope_dates(daily, regime_labels):
        gross_return = _compound(block["gross_return"])
        benchmark_return = _compound(block["benchmark_return"])
        for multiplier in spec.cost_multipliers:
            net = block["gross_return"] - multiplier * block["explicit_cost"]
            net_excess = block["gross_excess"] - multiplier * block["explicit_cost"]
            net_return = _compound(net)
            rows.append(
                {
                    "run": run_name,
                    "model": model,
                    "variant": variant,
                    "scope_type": scope_type,
                    "scope": scope,
                    "dimension": dimension,
                    "state": state,
                    "sessions": int(block["trade_date"].nunique()),
                    "cost_multiplier": multiplier,
                    "gross_return": gross_return,
                    "gross_excess_return": gross_return - benchmark_return,
                    "scaled_explicit_cost": float((multiplier * block["explicit_cost"]).sum()),
                    "net_return": net_return,
                    "net_excess_return": net_return - benchmark_return,
                    "excess_ir": _annualized_ir(net_excess, spec.annualization_sessions),
                    "max_drawdown": _max_drawdown(net),
                }
            )
    return pd.DataFrame(rows)


def _rolling_beta(
    strategy_return: pd.Series,
    benchmark_return: pd.Series,
    window: int,
) -> pd.Series:
    """Rolling regression beta of strategy return on benchmark return."""
    strategy = pd.to_numeric(strategy_return, errors="coerce")
    benchmark = pd.to_numeric(benchmark_return, errors="coerce")
    covariance = strategy.rolling(window, min_periods=window // 2).cov(benchmark)
    variance = benchmark.rolling(window, min_periods=window // 2).var()
    return (covariance / variance.replace(0.0, float("nan"))).replace([np.inf, -np.inf], np.nan)


def _rolling_compounded_excess(
    strategy_return: pd.Series,
    benchmark_return: pd.Series,
    window: int,
) -> pd.Series:
    """Rolling compounded net excess return over the trailing window."""
    strategy = pd.to_numeric(strategy_return, errors="coerce")
    benchmark = pd.to_numeric(benchmark_return, errors="coerce")
    excess = (
        (1.0 + strategy - benchmark)
        .rolling(window, min_periods=window // 2)
        .apply(
            lambda values: float(np.prod(values) - 1.0),
            raw=True,
        )
    )
    return excess.replace([np.inf, -np.inf], np.nan)


def derive_rolling_benchmark_diagnostics(
    daily: pd.DataFrame,
    *,
    run_name: str,
    model: str,
    variant: str,
    window: int = 63,
) -> pd.DataFrame:
    """Daily rolling benchmark diagnostics: beta and compounded excess.

    Answers whether beta drifts and in which phases alpha decayed, without
    requiring a separate risk model.  The daily bridge already aligns the
    portfolio return with its benchmark and fold assignment.
    """
    frame = daily.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise").dt.normalize()
    frame["signal_date"] = pd.to_datetime(frame["signal_date"], errors="raise").dt.normalize()
    frame["gross_return"] = pd.to_numeric(frame["gross_return"], errors="coerce")
    frame["benchmark_return"] = pd.to_numeric(frame["benchmark_return"], errors="coerce")
    beta = _rolling_beta(frame["gross_return"], frame["benchmark_return"], window)
    excess = _rolling_compounded_excess(frame["gross_return"], frame["benchmark_return"], window)
    result = frame[["trade_date", "signal_date", "fold"]].copy()
    result["run"] = run_name
    result["model"] = model
    result["variant"] = variant
    result["rolling_beta"] = beta.to_numpy()
    result["rolling_excess_return"] = excess.to_numpy()
    result["rolling_window_days"] = window
    return result


def derive_benchmark_diagnostics(
    daily: pd.DataFrame,
    regime_labels: pd.DataFrame,
    *,
    run_name: str,
    model: str,
    variant: str,
    spec: FailureAttributionSpec,
) -> pd.DataFrame:
    """Scope-level benchmark diagnostics: beta, tracking error, captures.

    Portfolio V2.3 adds these so the Rank Buffer candidate can be judged on
    whether it participated in the CSI300 rally (beta / up capture) rather than
    raw return alone.  Beta and captures use gross returns; tracking error uses
    the daily net-excess series, matching the rest of the attribution chain.
    """

    rows: list[dict[str, object]] = []
    for scope_type, scope, dimension, state, block in _scope_dates(daily, regime_labels):
        gross_return = pd.to_numeric(block["gross_return"], errors="coerce")
        benchmark_return = pd.to_numeric(block["benchmark_return"], errors="coerce")
        net_excess = pd.to_numeric(block["net_excess"], errors="coerce")
        paired = pd.concat([gross_return, benchmark_return], axis=1).dropna()
        variance = float(paired.iloc[:, 1].var(ddof=1)) if len(paired) > 1 else float("nan")
        beta = (
            float(paired.iloc[:, 0].cov(paired.iloc[:, 1]) / variance)
            if np.isfinite(variance) and variance > 0
            else float("nan")
        )
        benchmark_positive = (paired.iloc[:, 1] > 0).to_numpy()
        benchmark_negative = (paired.iloc[:, 1] < 0).to_numpy()
        up_capture = (
            float(paired.iloc[benchmark_positive, 0].mean() / paired.iloc[benchmark_positive, 1].mean())
            if benchmark_positive.any()
            else float("nan")
        )
        down_capture = (
            float(paired.iloc[benchmark_negative, 0].mean() / paired.iloc[benchmark_negative, 1].mean())
            if benchmark_negative.any()
            else float("nan")
        )
        rows.append(
            {
                "run": run_name,
                "model": model,
                "variant": variant,
                "scope_type": scope_type,
                "scope": scope,
                "dimension": dimension,
                "state": state,
                "sessions": int(block["trade_date"].nunique()),
                "portfolio_beta": beta,
                "tracking_error": _annualized_vol(net_excess, spec.annualization_sessions),
                "up_capture": up_capture,
                "down_capture": down_capture,
                "gross_active_return": _compound(block["gross_excess"]),
                "net_active_return": _compound(block["net_excess"]),
            }
        )
    return pd.DataFrame(rows)
