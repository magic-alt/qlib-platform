from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from .artifacts import ArtifactType, PromotionStatus, stamp_artifact, validate_artifact
from .portfolio import PortfolioPolicy, construct_target_portfolio


def _resolve_path(value: str | Path, base: Path) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fp:
        loaded = yaml.safe_load(fp) or {}
    if not isinstance(loaded, dict):
        raise ValueError("execution config must be a mapping")
    return loaded


def _selection_signal_date(frame: pd.DataFrame, path: Path, requested: str | None) -> pd.Timestamp:
    if requested:
        return pd.Timestamp(requested).normalize()
    if "signal_date" in frame.columns and frame["signal_date"].notna().any():
        values = pd.to_datetime(frame["signal_date"], errors="coerce").dropna().dt.normalize().unique()
        if len(values) != 1:
            raise ValueError(f"selection contains multiple signal dates: {values}")
        return pd.Timestamp(values[0])
    match = re.search(r"(20\d{6})", path.stem)
    if match:
        return pd.Timestamp(match.group(1)).normalize()
    raise ValueError("signal date is required in selection data, file name, or --selection-date")


def _next_trade_date(
    signal_date: pd.Timestamp, calendar_path: Path, explicit_trade_date: str | None = None
) -> pd.Timestamp:
    if explicit_trade_date:
        trade_date = pd.Timestamp(explicit_trade_date).normalize()
        if trade_date <= signal_date:
            raise ValueError("trade_date must be later than signal_date")
        return trade_date
    if not calendar_path.exists():
        raise FileNotFoundError(
            f"official trading calendar is required to resolve T+1 trade date: {calendar_path}. "
            "Business-day fallback is intentionally disabled."
        )
    cal = pd.read_parquet(calendar_path)
    if not {"cal_date", "is_open"}.issubset(cal.columns):
        raise ValueError("trading calendar must contain cal_date and is_open")
    dates = pd.to_datetime(
        cal.loc[pd.to_numeric(cal["is_open"], errors="coerce") == 1, "cal_date"], errors="coerce"
    )
    future = dates[dates.dt.normalize() > signal_date].sort_values()
    if future.empty:
        raise ValueError(f"trading calendar has no open date after {signal_date.date()}")
    return future.iloc[0].normalize()


def _find_selection(selection_dir: Path, pattern: str, requested_date: str | None) -> Path:
    if requested_date:
        key = pd.Timestamp(requested_date).strftime("%Y%m%d")
        matches = sorted(selection_dir.glob(pattern.replace("*", key)))
    else:
        matches = sorted(selection_dir.glob(pattern), key=lambda p: p.stat().st_mtime)
    if not matches:
        raise FileNotFoundError(f"no selection file found in {selection_dir} with pattern {pattern}")
    return matches[-1]


def _read_current(path: Path | None) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame(columns=["instrument", "target_weight"])
    frame = pd.read_csv(path)
    if "instrument" not in frame.columns:
        raise ValueError(f"current portfolio file lacks instrument: {path}")
    if "target_weight" not in frame.columns:
        if "current_weight" in frame.columns:
            frame = frame.rename(columns={"current_weight": "target_weight"})
        else:
            raise ValueError(f"current portfolio file lacks target_weight/current_weight: {path}")
    return frame[["instrument", "target_weight"]].drop_duplicates("instrument", keep="last")


def _atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(tmp, index=False, encoding="utf-8-sig")
    os.replace(tmp, path)


def _atomic_json(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def build_trade_plan(
    *,
    config_path: str | Path,
    selection_file: str | Path | None = None,
    selection_date: str | None = None,
    prev_selection_file: str | Path | None = None,
    allow_weight_update: bool | None = None,
    trade_date: str | None = None,
) -> tuple[Path, pd.DataFrame]:
    config_path = Path(config_path).expanduser().resolve()
    config = _load_yaml(config_path)
    project_dir = config_path.parent.parent
    execution = config.get("execution", config)
    if not isinstance(execution, dict):
        raise ValueError("execution section must be a mapping")

    selection_dir = _resolve_path(str(execution.get("selection_dir", "./data/output")), project_dir)
    output_dir = _resolve_path(str(execution.get("output_dir", "./data/output")), project_dir)
    selection_path = (
        Path(selection_file).expanduser().resolve()
        if selection_file
        else _find_selection(
            selection_dir, str(execution.get("selection_glob", "selection_*.csv")), selection_date
        )
    )
    selection = pd.read_csv(selection_path)
    metadata = validate_artifact(selection, ArtifactType.MODEL_TOPK)
    signal_ts = _selection_signal_date(selection, selection_path, selection_date)
    calendar_path = _resolve_path(
        str(execution.get("calendar_path", "./data/metadata/trade_calendar.parquet")), project_dir
    )
    trade_ts = _next_trade_date(signal_ts, calendar_path, trade_date)

    current_path = Path(prev_selection_file).expanduser().resolve() if prev_selection_file else None
    current = _read_current(current_path)
    policy_data = execution.get("portfolio", execution)
    policy = PortfolioPolicy.from_mapping(policy_data if isinstance(policy_data, dict) else {})
    targets = construct_target_portfolio(selection, policy, current=current)

    current_weights = (
        current.set_index("instrument")["target_weight"] if not current.empty else pd.Series(dtype=float)
    )
    target_weights = (
        targets.set_index("instrument")["target_weight"] if not targets.empty else pd.Series(dtype=float)
    )
    universe = current_weights.index.union(target_weights.index)
    threshold = float(execution.get("rebalance_threshold", 0.005))
    force_sell_removed = bool(execution.get("force_sell_removed", True))
    emit_weight = (
        bool(execution.get("allow_weight_update", True))
        if allow_weight_update is None
        else allow_weight_update
    )
    score_map = targets.set_index("instrument")["score"] if not targets.empty else pd.Series(dtype=float)

    rows: list[dict[str, object]] = []
    for instrument in sorted(universe):
        old = float(current_weights.get(instrument, 0.0))
        new = float(target_weights.get(instrument, 0.0))
        delta = new - old
        if new <= policy.min_position and old > policy.min_position:
            if not force_sell_removed:
                continue
            action, trigger = "SELL", "REMOVED_OR_BELOW_MINIMUM"
        elif old <= policy.min_position and new > policy.min_position:
            action, trigger = "BUY", "NEW_TARGET"
        elif emit_weight and abs(delta) >= threshold:
            action, trigger = "WEIGHT", "REBALANCE_THRESHOLD"
        else:
            continue
        rows.append(
            {
                "trade_date": trade_ts.strftime("%Y-%m-%d"),
                "signal_date": signal_ts.strftime("%Y-%m-%d"),
                "instrument": instrument,
                "action": action,
                "target_weight": new,
                "current_weight": old,
                "weight_delta": delta,
                "score": float(score_map.get(instrument, float("nan"))),
                "trigger": trigger,
                "model_id": str(selection["model_id"].dropna().iloc[0])
                if "model_id" in selection and selection["model_id"].notna().any()
                else "unversioned",
                "dataset_id": str(selection["dataset_id"].dropna().iloc[0])
                if "dataset_id" in selection and selection["dataset_id"].notna().any()
                else "unversioned",
            }
        )
    plan = pd.DataFrame(rows)
    if not plan.empty:
        action_order = {"SELL": 0, "BUY": 1, "WEIGHT": 2}
        plan["_order"] = plan["action"].map(action_order)
        plan = plan.sort_values(["_order", "score", "instrument"], ascending=[True, False, True]).drop(
            columns="_order"
        )
        plan = stamp_artifact(
            plan,
            ArtifactType.STRATEGY_DECISION,
            promotion_status=PromotionStatus.PROMOTED,
            run_id=metadata["run_id"],
            model_id=metadata["model_id"],
            dataset_id=metadata["dataset_id"],
            lineage_id=metadata["lineage_id"],
            manifest_path=metadata["manifest_path"],
        )

    date_key = trade_ts.strftime("%Y%m%d")
    plan_path = output_dir / f"trade_plan_{date_key}.csv"
    targets_path = output_dir / f"target_portfolio_{date_key}.csv"
    _atomic_csv(plan, plan_path)
    targets_out = targets.copy()
    targets_out["trade_date"] = trade_ts.strftime("%Y-%m-%d")
    targets_out["signal_date"] = signal_ts.strftime("%Y-%m-%d")
    lead = ["signal_date", "trade_date"]
    targets_out = targets_out[lead + [c for c in targets_out.columns if c not in lead]]
    targets_out = stamp_artifact(
        targets_out,
        ArtifactType.TARGET_PORTFOLIO,
        promotion_status=PromotionStatus.PROMOTED,
        run_id=metadata["run_id"],
        model_id=metadata["model_id"],
        dataset_id=metadata["dataset_id"],
        lineage_id=metadata["lineage_id"],
        manifest_path=metadata["manifest_path"],
    )
    _atomic_csv(targets_out, targets_path)
    _atomic_json(
        {
            "schema_version": "2.0",
            "artifact_type": ArtifactType.STRATEGY_DECISION.value,
            "source_artifact_type": ArtifactType.MODEL_TOPK.value,
            "promotion_status": PromotionStatus.PROMOTED.value,
            "lineage_id": metadata["lineage_id"],
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "selection_file": str(selection_path),
            "current_portfolio_file": str(current_path) if current_path else None,
            "signal_date": signal_ts.strftime("%Y-%m-%d"),
            "trade_date": trade_ts.strftime("%Y-%m-%d"),
            "target_count": int((targets_out["target_weight"] > 0).sum()) if not targets_out.empty else 0,
            "target_exposure": float(targets_out["target_weight"].sum()) if not targets_out.empty else 0.0,
            "plan_rows": len(plan),
            "policy": policy.__dict__,
        },
        output_dir / f"trade_plan_{date_key}.manifest.json",
    )
    return plan_path, plan
