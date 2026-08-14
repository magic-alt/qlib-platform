from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from .settings import Settings
from .symbols import ts_to_qlib


_REPORT_NAME = "backtest_report"
_ASSET_NAMES = (
    "performance.png",
    "pnl_drawdown.png",
    "exposure_positions.png",
    "final_holdings.png",
    "trade_activity.png",
)


@dataclass(frozen=True)
class ReportArtifacts:
    markdown_path: Path
    pdf_path: Path
    assets_dir: Path

    def manifest_entries(self) -> list[dict[str, object]]:
        return [
            {"name": self.markdown_path.name, "localPath": str(self.markdown_path)},
            {"name": self.pdf_path.name, "localPath": str(self.pdf_path)},
            {"name": self.assets_dir.name, "localPath": str(self.assets_dir)},
        ]


@dataclass(frozen=True)
class RunData:
    run_dir: Path
    manifest: dict[str, Any]
    report: pd.DataFrame
    audit: pd.DataFrame
    holdings: pd.DataFrame
    names: dict[str, str]


def export_holding_snapshots(positions: Mapping[pd.Timestamp, Any]) -> pd.DataFrame:
    """Make Qlib's serialized Position objects portable and report-friendly."""

    rows: list[dict[str, object]] = []
    for value in sorted(positions):
        trade_date = pd.Timestamp(value).normalize()
        position = positions[value]
        account = float(position.calculate_value())
        cash = float(position.get_cash())
        for instrument in sorted(position.get_stock_list()):
            code = str(instrument)
            quantity = float(position.get_stock_amount(code))
            price = float(position.get_stock_price(code))
            rows.append(
                {
                    "trade_date": trade_date,
                    "instrument": code,
                    "quantity": quantity,
                    "price": price,
                    "market_value": quantity * price,
                    "weight": float(position.get_stock_weight(code)),
                    "holding_days": int(position.get_stock_count(code, "day")),
                    "cash": cash,
                    "account": account,
                }
            )
    return pd.DataFrame(
        rows,
        columns=[
            "trade_date",
            "instrument",
            "quantity",
            "price",
            "market_value",
            "weight",
            "holding_days",
            "cash",
            "account",
        ],
    )


def _artifact_path(run_dir: Path, manifest: Mapping[str, Any], name: str) -> Path | None:
    for item in manifest.get("artifacts", []):
        if isinstance(item, Mapping) and item.get("name") == name:
            path = item.get("localPath")
            if path:
                return Path(str(path)).expanduser()
    candidate = run_dir / name
    return candidate if candidate.exists() else None


def _stock_names(settings: Settings) -> dict[str, str]:
    path = settings.paths.metadata / "stock_master.parquet"
    if not path.exists():
        return {}
    frame = pd.read_parquet(path)
    if not {"ts_code", "name"}.issubset(frame.columns):
        return {}
    names: dict[str, str] = {}
    for row in frame[["ts_code", "name"]].dropna().itertuples(index=False):
        try:
            names[ts_to_qlib(str(row.ts_code))] = str(row.name)
        except ValueError:
            continue
    return names


def _legacy_positions_path(settings: Settings, run_id: str, explicit: str | Path | None) -> Path:
    if explicit is not None:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"positions file not found: {path}")
        return path
    project_root = settings.config_path.parent.parent
    candidates: list[Path] = []
    for root in (project_root / "mlruns", settings.paths.models / "mlruns"):
        if root.exists():
            candidates.extend(root.glob(f"*/{run_id}/artifacts/portfolio_analysis/positions_normal_1day.pkl"))
    candidates = sorted({path.resolve() for path in candidates})
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise FileNotFoundError(
            "holding snapshots are missing. Re-run the backtest, or pass "
            "--positions-file path/to/positions_normal_1day.pkl."
        )
    raise RuntimeError(
        "multiple MLflow position snapshots match this run; pass --positions-file explicitly: "
        + ", ".join(str(path) for path in candidates)
    )


def _load_holdings(
    settings: Settings,
    run_dir: Path,
    manifest: Mapping[str, Any],
    positions_file: str | Path | None,
) -> pd.DataFrame:
    path = _artifact_path(run_dir, manifest, "holdings.parquet")
    if path is not None and path.exists():
        return pd.read_parquet(path)
    run_id = str(manifest.get("externalRunId", run_dir.name))
    position_path = _legacy_positions_path(settings, run_id, positions_file)
    try:
        import pickle

        with position_path.open("rb") as handle:
            positions = pickle.load(handle)
    except ImportError as exc:  # pragma: no cover - qlib is an optional dependency
        raise RuntimeError("Qlib is required to load a legacy positions pickle") from exc
    return export_holding_snapshots(positions)


def _normalise_report(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        raise ValueError("portfolio report is empty")
    report = frame.copy()
    report.index = pd.to_datetime(report.index, errors="raise").normalize()
    report.index.name = "trade_date"
    report = report[~report.index.duplicated(keep="last")].sort_index()
    if "account" not in report:
        raise ValueError("portfolio report missing account")
    report["account"] = pd.to_numeric(report["account"], errors="coerce")
    if report["account"].isna().any() or (report["account"] <= 0).any():
        raise ValueError("portfolio report has invalid account values")
    if "return" not in report:
        report["return"] = report["account"].pct_change().fillna(0.0)
    report["return"] = pd.to_numeric(report["return"], errors="coerce").fillna(0.0)
    bench = report["bench"] if "bench" in report else pd.Series(0.0, index=report.index)
    report["bench"] = pd.to_numeric(bench, errors="coerce").fillna(0.0)
    for column in ("cash", "value", "cost", "total_cost", "turnover", "total_turnover"):
        if column in report:
            report[column] = pd.to_numeric(report[column], errors="coerce")
    if "cash" not in report:
        report["cash"] = 0.0
    if "value" not in report:
        report["value"] = report["account"] - report["cash"].fillna(0.0)
    return report


def _normalise_audit(frame: pd.DataFrame, names: Mapping[str, str]) -> pd.DataFrame:
    audit = frame.copy()
    if audit.empty:
        return audit
    if "trade_date" not in audit or "instrument" not in audit:
        raise ValueError("strategy audit must contain trade_date and instrument")
    audit["trade_date"] = pd.to_datetime(audit["trade_date"], errors="raise").dt.normalize()
    audit["instrument"] = audit["instrument"].astype(str)
    audit["stock_name"] = audit["instrument"].map(names).fillna("")
    for column in (
        "quantity_before",
        "quantity_after",
        "requested_quantity",
        "filled_quantity",
        "filled_price",
        "filled_value",
        "trade_cost",
    ):
        if column not in audit:
            audit[column] = np.nan
        audit[column] = pd.to_numeric(audit[column], errors="coerce")
    if "order_requested" not in audit:
        audit["order_requested"] = False
    if "actual_action" not in audit:
        audit["actual_action"] = "HOLD"
    if "target_action" not in audit:
        audit["target_action"] = "HOLD"
    if "execution_status" not in audit:
        audit["execution_status"] = "UNKNOWN"
    if "action_reason" not in audit:
        audit["action_reason"] = ""
    return audit.sort_values(["trade_date", "instrument"], kind="stable").reset_index(drop=True)


def _normalise_holdings(frame: pd.DataFrame, names: Mapping[str, str]) -> pd.DataFrame:
    holdings = frame.copy()
    if holdings.empty:
        return pd.DataFrame(
            columns=[
                "trade_date",
                "instrument",
                "stock_name",
                "quantity",
                "price",
                "market_value",
                "weight",
                "holding_days",
                "cash",
                "account",
            ]
        )
    required = {"trade_date", "instrument", "quantity", "price", "weight", "holding_days"}
    missing = required - set(holdings.columns)
    if missing:
        raise ValueError(f"holding snapshots missing columns: {sorted(missing)}")
    holdings["trade_date"] = pd.to_datetime(holdings["trade_date"], errors="raise").dt.normalize()
    holdings["instrument"] = holdings["instrument"].astype(str)
    holdings["stock_name"] = holdings["instrument"].map(names).fillna("")
    for column in ("quantity", "price", "market_value", "weight", "holding_days", "cash", "account"):
        if column not in holdings:
            holdings[column] = np.nan
        holdings[column] = pd.to_numeric(holdings[column], errors="coerce")
    if holdings["market_value"].isna().any():
        holdings["market_value"] = holdings["market_value"].fillna(holdings["quantity"] * holdings["price"])
    return holdings.sort_values(
        ["trade_date", "weight", "instrument"], ascending=[True, False, True], kind="stable"
    )


def _load_price_factors(settings: Settings, audit: pd.DataFrame, holdings: pd.DataFrame) -> pd.DataFrame:
    key_frames = [frame[["trade_date", "instrument"]] for frame in (audit, holdings) if not frame.empty]
    if not key_frames:
        return pd.DataFrame(columns=["trade_date", "instrument", "price_factor"])
    keys = pd.concat(key_frames, ignore_index=True).drop_duplicates()
    rows: list[pd.DataFrame] = []
    for instrument, group in keys.groupby("instrument", sort=False):
        path = settings.paths.staging_full / f"{instrument}.parquet"
        if not path.is_file():
            continue
        factors = pd.read_parquet(path, columns=["date", "factor"])
        factors["trade_date"] = pd.to_datetime(factors["date"], errors="raise").dt.normalize()
        factors["price_factor"] = pd.to_numeric(factors["factor"], errors="coerce")
        factors = factors.sort_values("trade_date", kind="stable")
        factors["price_factor"] = factors["price_factor"].ffill()
        wanted = pd.DatetimeIndex(group["trade_date"].unique())
        selected = factors.loc[factors["trade_date"].isin(wanted), ["trade_date", "price_factor"]].copy()
        if selected.empty:
            continue
        selected["instrument"] = str(instrument)
        rows.append(selected)
    if not rows:
        return pd.DataFrame(columns=["trade_date", "instrument", "price_factor"])
    return pd.concat(rows, ignore_index=True).drop_duplicates(["trade_date", "instrument"], keep="last")


def _restore_raw_trade_units(
    settings: Settings, audit: pd.DataFrame, holdings: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Convert Qlib-normalized prices and quantities back to CNY and shares."""

    factors = _load_price_factors(settings, audit, holdings)
    if factors.empty:
        return audit, holdings

    def restore(
        frame: pd.DataFrame, *, price_columns: tuple[str, ...], quantity_columns: tuple[str, ...]
    ) -> pd.DataFrame:
        if frame.empty:
            return frame
        restored = frame.merge(
            factors, on=["trade_date", "instrument"], how="left", validate="many_to_one", sort=False
        )
        valid = restored["price_factor"].notna() & restored["price_factor"].gt(0)
        for column in price_columns:
            if column in restored:
                restored.loc[valid, column] = (
                    restored.loc[valid, column] / restored.loc[valid, "price_factor"]
                )
        for column in quantity_columns:
            if column in restored:
                restored.loc[valid, column] = (
                    restored.loc[valid, column] * restored.loc[valid, "price_factor"]
                )
        return restored.drop(columns="price_factor")

    return (
        restore(
            audit,
            price_columns=("filled_price",),
            quantity_columns=(
                "quantity_before",
                "quantity_after",
                "requested_quantity",
                "filled_quantity",
            ),
        ),
        restore(holdings, price_columns=("price",), quantity_columns=("quantity",)),
    )


def load_run_data(
    settings: Settings,
    run_dir: str | Path,
    *,
    positions_file: str | Path | None = None,
) -> RunData:
    directory = Path(run_dir).expanduser().resolve()
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"run manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    names = _stock_names(settings)
    report_path = _artifact_path(directory, manifest, "portfolio_report.parquet")
    audit_path = _artifact_path(directory, manifest, "strategy_audit.parquet")
    if report_path is None or not report_path.exists():
        raise FileNotFoundError("portfolio_report.parquet is required for a backtest report")
    if audit_path is None or not audit_path.exists():
        raise FileNotFoundError("strategy_audit.parquet is required for a full backtest report")
    audit = _normalise_audit(pd.read_parquet(audit_path), names)
    holdings = _normalise_holdings(_load_holdings(settings, directory, manifest, positions_file), names)
    audit, holdings = _restore_raw_trade_units(settings, audit, holdings)
    return RunData(
        run_dir=directory,
        manifest=manifest,
        report=_normalise_report(pd.read_parquet(report_path)),
        audit=audit,
        holdings=holdings,
        names=names,
    )


def _metric_values(report: pd.DataFrame, audit: pd.DataFrame) -> dict[str, float | int | None]:
    account = report["account"]
    returns = report["return"]
    benchmark = (1.0 + report["bench"]).cumprod()
    periods = max(len(report) - 1, 1)
    total_return = float(account.iloc[-1] / account.iloc[0] - 1.0)
    bench_return = float(benchmark.iloc[-1] - 1.0)
    annual_return = float((1.0 + total_return) ** (252.0 / periods) - 1.0)
    volatility = float(returns.iloc[1:].std(ddof=1) * math.sqrt(252)) if len(report) > 2 else None
    std = returns.iloc[1:].std(ddof=1) if len(report) > 2 else np.nan
    sharpe = float(returns.iloc[1:].mean() / std * math.sqrt(252)) if pd.notna(std) and std > 0 else None
    drawdown = account / account.cummax() - 1.0
    active_returns = returns.iloc[1:]
    orders = audit.loc[audit["order_requested"].fillna(False).astype(bool)] if not audit.empty else audit
    filled = (
        orders.loc[orders["execution_status"].isin(["FILLED", "PARTIAL"])] if not orders.empty else orders
    )
    return {
        "initial_account": float(account.iloc[0]),
        "ending_account": float(account.iloc[-1]),
        "profit_loss": float(account.iloc[-1] - account.iloc[0]),
        "total_return": total_return,
        "annual_return": annual_return,
        "benchmark_return": bench_return,
        "excess_return": float(total_return - bench_return),
        "annual_volatility": volatility,
        "sharpe": sharpe,
        "max_drawdown": float(drawdown.min()),
        "total_cost": (
            float(report["total_cost"].dropna().iloc[-1])
            if "total_cost" in report and report["total_cost"].notna().any()
            else None
        ),
        "total_turnover": (
            float(report["total_turnover"].dropna().iloc[-1])
            if "total_turnover" in report and report["total_turnover"].notna().any()
            else None
        ),
        "daily_win_rate": float((active_returns > 0).mean()) if len(active_returns) else None,
        "order_count": int(len(orders)),
        "filled_order_count": int(len(filled)),
    }


def _plot_font() -> object | None:
    from matplotlib import font_manager

    families = (
        "Microsoft YaHei",
        "SimHei",
        "Noto Sans CJK SC",
        "Noto Sans CJK JP",
        "Source Han Sans SC",
        "PingFang SC",
        "Hiragino Sans GB",
        "WenQuanYi Micro Hei",
        "Arial Unicode MS",
    )
    for family in families:
        try:
            path = Path(
                font_manager.findfont(
                    font_manager.FontProperties(family=family),
                    fallback_to_default=False,
                )
            )
        except (RuntimeError, ValueError):
            continue
        if path.is_file():
            return font_manager.FontProperties(fname=str(path))

    candidates = (
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    )
    for value in candidates:
        path = Path(value)
        if path.exists():
            try:
                return font_manager.FontProperties(fname=str(path))
            except RuntimeError:
                continue
    return None


def _set_title(axis: Any, title: str, font: object | None) -> None:
    label = axis.set_title(title)
    if font is not None:
        label.set_fontproperties(font)


def _apply_tick_font(axis: Any, font: object | None) -> None:
    if font is None:
        return
    for label in axis.get_xticklabels() + axis.get_yticklabels():
        label.set_fontproperties(font)


def _save_charts(data: RunData, assets_dir: Path) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    for path in assets_dir.glob("*.png"):
        path.unlink()
    assets_dir.mkdir(parents=True, exist_ok=True)
    font = _plot_font()
    report = data.report
    account = report["account"]
    net_value = account / account.iloc[0]
    benchmark = (1.0 + report["bench"]).cumprod()
    drawdown = net_value / net_value.cummax() - 1.0
    date = report.index
    paths: list[Path] = []

    fig, ax = plt.subplots(figsize=(11.5, 5.4))
    ax.plot(date, net_value, label="Strategy", linewidth=2.1, color="#176B87")
    ax.plot(date, benchmark, label="Benchmark", linewidth=1.6, color="#D97706")
    _set_title(ax, "策略与基准净值", font)
    ax.set_ylabel("Net value")
    ax.grid(alpha=0.22)
    ax.legend()
    fig.autofmt_xdate()
    path = assets_dir / "performance.png"
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    paths.append(path)

    fig, axes = plt.subplots(2, 1, figsize=(11.5, 7.2), sharex=True, height_ratios=[1.2, 1])
    axes[0].plot(date, (account - account.iloc[0]) / 1_000_000, color="#176B87", linewidth=2)
    axes[0].axhline(0, color="#6B7280", linewidth=0.8)
    _set_title(axes[0], "账户累计盈亏", font)
    axes[0].set_ylabel("CNY million")
    axes[0].grid(alpha=0.22)
    axes[1].fill_between(date, drawdown, 0, color="#DC2626", alpha=0.22)
    axes[1].plot(date, drawdown, color="#DC2626", linewidth=1.3)
    _set_title(axes[1], "回撤", font)
    axes[1].set_ylabel("Drawdown")
    axes[1].yaxis.set_major_formatter(lambda value, _: f"{value:.0%}")
    axes[1].grid(alpha=0.22)
    fig.autofmt_xdate()
    fig.tight_layout()
    path = assets_dir / "pnl_drawdown.png"
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    paths.append(path)

    positions_count = (
        data.holdings.groupby("trade_date")["instrument"].nunique()
        if not data.holdings.empty
        else pd.Series(dtype=float)
    )
    count = positions_count.reindex(date, fill_value=0)
    value = report["value"].fillna(account - report["cash"].fillna(0.0))
    cash = report["cash"].fillna(0.0)
    fig, axes = plt.subplots(2, 1, figsize=(11.5, 7.2), sharex=True)
    axes[0].stackplot(
        date,
        value / account,
        cash / account,
        labels=["Stock value", "Cash"],
        colors=["#176B87", "#EAB308"],
        alpha=0.85,
    )
    axes[0].set_ylim(0, 1.05)
    _set_title(axes[0], "账户仓位与现金占比", font)
    axes[0].set_ylabel("Account share")
    axes[0].legend(loc="upper right")
    axes[0].grid(alpha=0.2)
    axes[1].plot(date, count, color="#7C3AED", linewidth=1.8)
    _set_title(axes[1], "每日持仓股票数量", font)
    axes[1].set_ylabel("Positions")
    axes[1].grid(alpha=0.22)
    fig.autofmt_xdate()
    fig.tight_layout()
    path = assets_dir / "exposure_positions.png"
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    paths.append(path)

    final = _final_holdings(data.holdings)
    fig, ax = plt.subplots(figsize=(11.5, 6.2))
    if final.empty:
        ax.text(0.5, 0.5, "No end-of-period positions", ha="center", va="center")
    else:
        top = final.head(15).sort_values("weight")
        label = [f"{row.stock_name or ''} {row.instrument}".strip() for row in top.itertuples(index=False)]
        ax.barh(label, top["weight"] * 100, color="#176B87")
        ax.set_xlabel("Weight (%)")
        _set_title(ax, "期末持仓权重 Top 15", font)
        ax.grid(axis="x", alpha=0.22)
        _apply_tick_font(ax, font)
    fig.tight_layout()
    path = assets_dir / "final_holdings.png"
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    paths.append(path)

    trades = _trade_rows(data.audit)
    fig, axes = plt.subplots(2, 1, figsize=(11.5, 7.2), sharex=True)
    if trades.empty:
        axes[0].text(0.5, 0.5, "No requested orders", ha="center", va="center")
        axes[1].text(0.5, 0.5, "No requested orders", ha="center", va="center")
    else:
        activity = (
            trades.assign(
                buy=trades["actual_action"].eq("BUY").astype(int),
                sell=trades["actual_action"].eq("SELL").astype(int),
            )
            .groupby("trade_date")[["buy", "sell"]]
            .sum()
        )
        axes[0].bar(activity.index, activity["buy"], label="Buy", color="#15803D", width=0.8)
        axes[0].bar(activity.index, -activity["sell"], label="Sell", color="#DC2626", width=0.8)
        _set_title(axes[0], "每日交易股票数", font)
        axes[0].set_ylabel("Buy / Sell")
        axes[0].legend()
        axes[0].grid(alpha=0.22)
        turnover = report["turnover"].fillna(0.0)
        axes[1].bar(date, turnover * 100, color="#7C3AED", width=0.8)
        _set_title(axes[1], "每日换手率", font)
        axes[1].set_ylabel("Turnover (%)")
        axes[1].grid(alpha=0.22)
    fig.autofmt_xdate()
    fig.tight_layout()
    path = assets_dir / "trade_activity.png"
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    paths.append(path)
    return paths


def _final_holdings(holdings: pd.DataFrame) -> pd.DataFrame:
    if holdings.empty:
        return holdings
    date = holdings["trade_date"].max()
    return holdings.loc[holdings["trade_date"] == date].sort_values("weight", ascending=False, kind="stable")


def _trade_rows(audit: pd.DataFrame) -> pd.DataFrame:
    if audit.empty:
        return audit
    requested = audit["order_requested"].fillna(False).astype(bool)
    actual = audit["actual_action"].isin(["BUY", "SELL"])
    return audit.loc[requested | actual].copy().sort_values(["trade_date", "instrument"], kind="stable")


def _fmt_number(value: object, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(str(value)):,.{digits}f}"


def _fmt_percent(value: object) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(str(value)):.2%}"


def _stock_label(row: Any) -> str:
    name = str(getattr(row, "stock_name", "") or "")
    code = str(getattr(row, "instrument", ""))
    return f"{name} ({code})" if name else code


def _target_stock_label(names: Mapping[str, str], item: Mapping[str, object]) -> str:
    code = str(item.get("instrument", ""))
    name = names.get(code, "")
    return f"{name} ({code})" if name else code


def _markdown_table(headers: Iterable[str], rows: Iterable[Iterable[object]]) -> str:
    header = list(headers)
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * len(header)) + " |"]
    for row in rows:
        cells = [str(value).replace("|", "\\|").replace("\n", "<br>") for value in row]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _relative(path: Path, from_dir: Path) -> str:
    return os.path.relpath(path, start=from_dir).replace(os.sep, "/")


def _metrics_rows(metrics: Mapping[str, float | int | None]) -> list[tuple[str, str]]:
    return [
        ("起始账户", _fmt_number(metrics["initial_account"])),
        ("期末账户", _fmt_number(metrics["ending_account"])),
        ("账户盈亏", _fmt_number(metrics["profit_loss"])),
        ("策略总收益", _fmt_percent(metrics["total_return"])),
        ("年化收益", _fmt_percent(metrics["annual_return"])),
        ("基准总收益", _fmt_percent(metrics["benchmark_return"])),
        ("总超额收益", _fmt_percent(metrics["excess_return"])),
        ("年化波动率", _fmt_percent(metrics["annual_volatility"])),
        ("Sharpe (rf=0)", _fmt_number(metrics["sharpe"])),
        ("最大回撤", _fmt_percent(metrics["max_drawdown"])),
        ("累计交易成本", _fmt_number(metrics["total_cost"])),
        ("累计换手金额", _fmt_number(metrics["total_turnover"])),
        ("日度胜率", _fmt_percent(metrics["daily_win_rate"])),
        ("请求订单 / 已成交", f"{metrics['order_count']} / {metrics['filled_order_count']}"),
    ]


def _runtime_rows(manifest: Mapping[str, Any]) -> list[tuple[str, str]]:
    runtime = manifest.get("runtime", {})
    if not isinstance(runtime, Mapping) or not runtime:
        return []
    versions = runtime.get("versions", {})
    version_text = (
        ", ".join(f"{key}={value}" for key, value in versions.items())
        if isinstance(versions, Mapping)
        else "-"
    )
    return [
        ("模型 Profile", str(runtime.get("modelProfile", "unknown"))),
        ("模型家族", str(runtime.get("modelFamily", "unknown"))),
        ("请求设备", str(runtime.get("requestedDevice", "unknown"))),
        ("实际设备", str(runtime.get("resolvedDevice", "unknown"))),
        ("降级原因", str(runtime.get("fallbackReason") or "-")),
        ("MPS CPU fallback", "开启" if runtime.get("mpsFallbackEnabled") else "关闭"),
        ("版本", version_text or "-"),
    ]


def _timing_rows(timings: object) -> list[tuple[str, str]]:
    if not isinstance(timings, Mapping):
        return []
    phases = timings.get("phasesSeconds", {})
    if not isinstance(phases, Mapping):
        return []
    labels = {
        "data_seconds": "数据初始化与准备",
        "train_seconds": "模型训练",
        "predict_seconds": "模型预测",
        "signal_analysis_seconds": "信号分析",
        "backtest_seconds": "策略回测与组合分析",
        "artifact_export_seconds": "研究产物导出",
    }
    rows = [(labels.get(str(key), str(key)), f"{float(value):.3f} s") for key, value in phases.items()]
    if "totalSeconds" in timings:
        rows.append(("阶段合计", f"{float(timings['totalSeconds']):.3f} s"))
    if "orchestrationWallSeconds" in timings:
        rows.append(("本次调度 wall time", f"{float(timings['orchestrationWallSeconds']):.3f} s"))
    return rows


def _fold_timing_rows(manifest: Mapping[str, Any]) -> list[list[str]]:
    rows: list[list[str]] = []
    for item in manifest.get("componentRuns", []):
        if not isinstance(item, Mapping):
            continue
        timings = item.get("timings", {})
        phases = timings.get("phasesSeconds", {}) if isinstance(timings, Mapping) else {}
        rows.append(
            [
                str(item.get("key", "")),
                "是" if item.get("checkpointReused") else "否",
                f"{float(phases.get('data_seconds', 0.0)):.2f}",
                f"{float(phases.get('train_seconds', 0.0)):.2f}",
                f"{float(phases.get('predict_seconds', 0.0)):.2f}",
                f"{float(phases.get('backtest_seconds', 0.0)):.2f}",
                f"{float(timings.get('totalSeconds', 0.0)):.2f}",
            ]
        )
    return rows


def _fold_rows(data: RunData) -> list[list[str]]:
    if "fold_key" not in data.report:
        return []
    rows: list[list[str]] = []
    for key, frame in data.report.groupby("fold_key", sort=False):
        audit = (
            data.audit.loc[data.audit["fold_key"] == key]
            if "fold_key" in data.audit
            else data.audit.iloc[0:0]
        )
        metrics = _metric_values(frame, audit)
        rows.append(
            [
                str(key),
                str(frame.index.min().date()),
                str(frame.index.max().date()),
                _fmt_percent(metrics["total_return"]),
                _fmt_percent(metrics["max_drawdown"]),
                f"{metrics['order_count']} / {metrics['filled_order_count']}",
            ]
        )
    return rows


def _write_markdown(
    data: RunData, artifacts: ReportArtifacts, metrics: Mapping[str, float | int | None]
) -> None:
    report = data.report
    final = _final_holdings(data.holdings)
    trades = _trade_rows(data.audit)
    lines = [
        "# 回测报告",
        "",
        "## 运行概览",
        "",
        _markdown_table(
            ["项目", "内容"],
            [
                ("运行 ID", data.manifest.get("externalRunId", data.run_dir.name)),
                ("运行类型", data.manifest.get("runKind", "unknown")),
                ("回测区间", f"{report.index.min():%Y-%m-%d} 至 {report.index.max():%Y-%m-%d}"),
                ("模型", data.manifest.get("model", {}).get("name", "unknown")),
                ("基准", data.manifest.get("execution", {}).get("benchmark", "unknown")),
                ("成交价", data.manifest.get("execution", {}).get("dealPrice", "unknown")),
            ],
        ),
        "",
    ]
    runtime_rows = _runtime_rows(data.manifest)
    timing_rows = _timing_rows(data.manifest.get("timings"))
    if runtime_rows or timing_rows:
        lines.extend(["## 运行环境与阶段耗时", ""])
        if runtime_rows:
            lines.extend([_markdown_table(["项目", "内容"], runtime_rows), ""])
        if timing_rows:
            lines.extend([_markdown_table(["阶段", "耗时"], timing_rows), ""])
    fold_timing_rows = _fold_timing_rows(data.manifest)
    if fold_timing_rows:
        lines.extend(
            [
                "### Walk-forward 分折耗时",
                "",
                _markdown_table(
                    ["折", "复用 checkpoint", "数据(s)", "训练(s)", "预测(s)", "回测(s)", "合计(s)"],
                    fold_timing_rows,
                ),
                "",
            ]
        )
    lines.extend(
        [
            "## 核心指标",
            "",
            _markdown_table(["指标", "数值"], _metrics_rows(metrics)),
            "",
            "## 图表",
            "",
            "![策略与基准净值](report_assets/performance.png)",
            "",
            "![账户盈亏与回撤](report_assets/pnl_drawdown.png)",
            "",
            "![账户仓位与持仓数](report_assets/exposure_positions.png)",
            "",
            "![期末持仓权重](report_assets/final_holdings.png)",
            "",
            "![每日交易活动](report_assets/trade_activity.png)",
            "",
            "## 期末持仓",
            "",
        ]
    )
    holding_rows = [
        [
            str(row.trade_date.date()),
            _stock_label(row),
            _fmt_percent(row.weight),
            _fmt_number(row.market_value),
            _fmt_number(row.quantity, 0),
            _fmt_number(row.price, 4),
            _fmt_number(row.holding_days, 0),
        ]
        for row in final.itertuples(index=False)
    ]
    lines.append(
        _markdown_table(["日期", "股票", "权重", "市值", "数量", "价格", "持有天数"], holding_rows)
        if holding_rows
        else "回测期末无持仓。"
    )
    fold_rows = _fold_rows(data)
    if fold_rows:
        lines.extend(
            [
                "",
                "## Walk-forward 分折汇总",
                "",
                "总账户净值按日收益连续复利重建。仓位与逐笔交易仍按各折独立模拟资金展示。",
                "",
                _markdown_table(["折", "开始", "结束", "收益", "最大回撤", "请求 / 成交"], fold_rows),
            ]
        )
    lines.extend(["", "## 逐笔委托与成交", ""])
    trade_rows = [
        [
            str(row.trade_date.date()),
            str(getattr(row, "fold_key", "")),
            _stock_label(row),
            row.target_action,
            row.actual_action,
            _fmt_number(row.requested_quantity, 0),
            _fmt_number(row.filled_quantity, 0),
            _fmt_number(row.filled_price, 4),
            _fmt_number(row.filled_value),
            _fmt_number(row.trade_cost),
            row.execution_status,
            row.action_reason,
        ]
        for row in trades.itertuples(index=False)
    ]
    lines.append(
        _markdown_table(
            [
                "日期",
                "折",
                "股票",
                "计划",
                "实际",
                "请求数量",
                "成交数量",
                "成交价",
                "成交额",
                "成本",
                "状态",
                "原因",
            ],
            trade_rows,
        )
        if trade_rows
        else "回测期间没有请求订单。"
    )
    targets = data.manifest.get("latestTargets", {}).get("targets", [])
    if targets:
        lines.extend(["", "## 最新目标组合", ""])
        target_rows = [
            [
                _target_stock_label(data.names, item),
                _fmt_percent(item.get("targetWeight")),
                _fmt_number(item.get("score"), 6),
            ]
            for item in targets
            if isinstance(item, Mapping)
        ]
        lines.append(_markdown_table(["股票", "目标权重", "模型分数"], target_rows))
    lines.extend(["", "## 产物索引", ""])
    artifact_rows = []
    for item in data.manifest.get("artifacts", []):
        if not isinstance(item, Mapping) or not item.get("localPath"):
            continue
        path = Path(str(item["localPath"]))
        artifact_rows.append(
            [str(item.get("name", path.name)), f"[{path.name}]({_relative(path, data.run_dir)})"]
        )
    lines.append(_markdown_table(["产物", "路径"], artifact_rows))
    artifacts.markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _pdf_table(rows: list[list[str]], widths: list[float], small: bool = False) -> Any:
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    table = Table(rows, colWidths=widths, repeatRows=1, splitByRow=1)
    size = 6.5 if small else 8
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "STSong-Light"),
                ("FONTSIZE", (0, 0), (-1, -1), size),
                ("LEADING", (0, 0), (-1, -1), size + 2),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#176B87")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D1D5DB")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _write_pdf(data: RunData, artifacts: ReportArtifacts, metrics: Mapping[str, float | int | None]) -> None:
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer
    except ImportError as exc:  # pragma: no cover - dependency check is packaging-owned
        raise RuntimeError(
            "PDF reporting requires reportlab. Install project dependencies and retry."
        ) from exc

    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    except Exception as exc:  # pragma: no cover - ReportLab bundles the CID font
        raise RuntimeError("ReportLab could not register its bundled CJK font") from exc

    page_width, page_height = landscape(A4)
    doc = SimpleDocTemplate(
        str(artifacts.pdf_path),
        pagesize=landscape(A4),
        leftMargin=13 * mm,
        rightMargin=13 * mm,
        topMargin=13 * mm,
        bottomMargin=13 * mm,
        title="回测报告",
        author="tushare-qlib",
    )
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "ReportTitle",
        parent=styles["Title"],
        fontName="STSong-Light",
        fontSize=22,
        leading=28,
        alignment=TA_CENTER,
    )
    heading = ParagraphStyle(
        "ReportHeading",
        parent=styles["Heading2"],
        fontName="STSong-Light",
        fontSize=13,
        leading=18,
        textColor=colors.HexColor("#176B87"),
    )
    body = ParagraphStyle(
        "ReportBody", parent=styles["BodyText"], fontName="STSong-Light", fontSize=8.5, leading=13
    )
    story: list[Any] = [
        Paragraph("回测报告", title),
        Spacer(1, 5 * mm),
        Paragraph(
            f"运行 ID: {data.manifest.get('externalRunId', data.run_dir.name)}    "
            f"区间: {data.report.index.min():%Y-%m-%d} 至 {data.report.index.max():%Y-%m-%d}",
            body,
        ),
        Spacer(1, 3 * mm),
    ]
    runtime_rows = _runtime_rows(data.manifest)
    timing_rows = _timing_rows(data.manifest.get("timings"))
    if runtime_rows or timing_rows:
        story.extend([Paragraph("运行环境与阶段耗时", heading), Spacer(1, 2 * mm)])
        if runtime_rows:
            story.extend(
                [
                    _pdf_table(
                        [["项目", "内容"]] + [[key, value] for key, value in runtime_rows],
                        [75 * mm, 185 * mm],
                    ),
                    Spacer(1, 3 * mm),
                ]
            )
        if timing_rows:
            story.extend(
                [
                    _pdf_table(
                        [["阶段", "耗时"]] + [[key, value] for key, value in timing_rows],
                        [155 * mm, 105 * mm],
                    ),
                    Spacer(1, 3 * mm),
                ]
            )
    story.extend(
        [
            Paragraph("核心指标", heading),
            Spacer(1, 2 * mm),
            _pdf_table(
                [["指标", "数值"]] + [[key, value] for key, value in _metrics_rows(metrics)],
                [155 * mm, 105 * mm],
            ),
        ]
    )
    for filename, caption in zip(
        _ASSET_NAMES,
        ["策略与基准净值", "账户盈亏与回撤", "账户仓位与持仓数", "期末持仓权重", "每日交易活动"],
        strict=True,
    ):
        story.extend([PageBreak(), Paragraph(caption, heading), Spacer(1, 2 * mm)])
        story.append(Image(str(artifacts.assets_dir / filename), width=258 * mm, height=145 * mm))

    final = _final_holdings(data.holdings)
    story.extend([PageBreak(), Paragraph("期末持仓", heading), Spacer(1, 2 * mm)])
    final_rows = [["日期", "股票", "权重", "市值", "数量", "价格", "持有天数"]]
    final_rows.extend(
        [
            [
                str(row.trade_date.date()),
                _stock_label(row),
                _fmt_percent(row.weight),
                _fmt_number(row.market_value),
                _fmt_number(row.quantity, 0),
                _fmt_number(row.price, 4),
                _fmt_number(row.holding_days, 0),
            ]
            for row in final.itertuples(index=False)
        ]
    )
    story.append(_pdf_table(final_rows, [25 * mm, 55 * mm, 22 * mm, 40 * mm, 38 * mm, 35 * mm, 25 * mm]))

    fold_rows = _fold_rows(data)
    if fold_rows:
        story.extend([PageBreak(), Paragraph("Walk-forward 分折汇总", heading), Spacer(1, 2 * mm)])
        story.append(
            Paragraph("总账户净值按日收益连续复利重建。仓位与订单明细按各折独立模拟资金保留。", body)
        )
        story.append(Spacer(1, 2 * mm))
        story.append(
            _pdf_table(
                [["折", "开始", "结束", "收益", "最大回撤", "请求 / 成交"]] + fold_rows,
                [42 * mm, 35 * mm, 35 * mm, 35 * mm, 35 * mm, 40 * mm],
            )
        )
        fold_timing_rows = _fold_timing_rows(data.manifest)
        if fold_timing_rows:
            story.extend([Spacer(1, 4 * mm), Paragraph("分折耗时（秒）", body), Spacer(1, 2 * mm)])
            story.append(
                _pdf_table(
                    [["折", "复用", "数据", "训练", "预测", "回测", "合计"]] + fold_timing_rows,
                    [45 * mm, 25 * mm, 32 * mm, 32 * mm, 32 * mm, 32 * mm, 32 * mm],
                    small=True,
                )
            )
        for key in data.report["fold_key"].dropna().drop_duplicates():
            subset = data.report.loc[data.report["fold_key"] == key]
            subset_holdings = (
                data.holdings.loc[data.holdings["fold_key"] == key]
                if "fold_key" in data.holdings
                else data.holdings.iloc[0:0]
            )
            story.extend([PageBreak(), Paragraph(f"分折: {key}", heading), Spacer(1, 2 * mm)])
            subset_audit = (
                data.audit.loc[data.audit["fold_key"] == key]
                if "fold_key" in data.audit
                else data.audit.iloc[0:0]
            )
            subset_metrics = _metric_values(subset, subset_audit)
            story.append(
                _pdf_table(
                    [["指标", "数值"]]
                    + [[name, value] for name, value in _metrics_rows(subset_metrics)[:10]],
                    [155 * mm, 105 * mm],
                )
            )
            fold_final = _final_holdings(subset_holdings)
            if not fold_final.empty:
                story.append(Spacer(1, 3 * mm))
                story.append(Paragraph("折末持仓 Top 10", body))
                rows = [["股票", "权重", "市值", "持有天数"]] + [
                    [
                        _stock_label(row),
                        _fmt_percent(row.weight),
                        _fmt_number(row.market_value),
                        _fmt_number(row.holding_days, 0),
                    ]
                    for row in fold_final.head(10).itertuples(index=False)
                ]
                story.append(_pdf_table(rows, [85 * mm, 35 * mm, 50 * mm, 35 * mm]))

    trades = _trade_rows(data.audit)
    story.extend([PageBreak(), Paragraph("逐笔委托与成交", heading), Spacer(1, 2 * mm)])
    trade_rows = [
        ["日期", "折", "股票", "计划", "实际", "请求", "成交", "成交价", "成交额", "成本", "状态", "原因"]
    ]
    trade_rows.extend(
        [
            [
                str(row.trade_date.date()),
                str(getattr(row, "fold_key", "")),
                _stock_label(row),
                str(row.target_action),
                str(row.actual_action),
                _fmt_number(row.requested_quantity, 0),
                _fmt_number(row.filled_quantity, 0),
                _fmt_number(row.filled_price, 4),
                _fmt_number(row.filled_value),
                _fmt_number(row.trade_cost),
                str(row.execution_status),
                str(row.action_reason),
            ]
            for row in trades.itertuples(index=False)
        ]
    )
    story.append(
        _pdf_table(
            trade_rows,
            [
                21 * mm,
                21 * mm,
                38 * mm,
                16 * mm,
                16 * mm,
                22 * mm,
                22 * mm,
                20 * mm,
                28 * mm,
                24 * mm,
                22 * mm,
                40 * mm,
            ],
            small=True,
        )
    )

    def _footer(canvas: Any, document: Any) -> None:
        canvas.saveState()
        canvas.setFont("STSong-Light", 7)
        canvas.setFillColor(colors.HexColor("#6B7280"))
        canvas.drawString(13 * mm, 8 * mm, f"{data.manifest.get('externalRunId', data.run_dir.name)}")
        canvas.drawRightString(page_width - 13 * mm, 8 * mm, f"第 {document.page} 页")
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(artifacts.pdf_path))
        if not reader.pages:
            raise RuntimeError("generated PDF has no pages")
    except ImportError:
        # ReportLab has completed the PDF; pypdf is a verification enhancement.
        pass


def write_backtest_report(
    settings: Settings,
    run_dir: str | Path,
    *,
    positions_file: str | Path | None = None,
) -> ReportArtifacts:
    """Generate the Markdown, chart, and PDF report for one completed run."""

    directory = Path(run_dir).expanduser().resolve()
    artifacts = ReportArtifacts(
        markdown_path=directory / f"{_REPORT_NAME}.md",
        pdf_path=directory / f"{_REPORT_NAME}.pdf",
        assets_dir=directory / "report_assets",
    )
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"run manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    existing = {str(item.get("name")) for item in manifest.get("artifacts", []) if isinstance(item, Mapping)}
    additions = [item for item in artifacts.manifest_entries() if str(item["name"]) not in existing]
    if additions:
        manifest.setdefault("artifacts", []).extend(additions)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    data = load_run_data(settings, directory, positions_file=positions_file)
    matplotlib_cache = settings.paths.state / "matplotlib"
    font_cache = settings.paths.state / "fontconfig"
    matplotlib_cache.mkdir(parents=True, exist_ok=True)
    font_cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(matplotlib_cache))
    os.environ.setdefault("XDG_CACHE_HOME", str(font_cache))
    artifacts.assets_dir.mkdir(parents=True, exist_ok=True)
    _save_charts(data, artifacts.assets_dir)
    metrics = _metric_values(data.report, data.audit)
    _write_markdown(data, artifacts, metrics)
    _write_pdf(data, artifacts, metrics)
    return artifacts
