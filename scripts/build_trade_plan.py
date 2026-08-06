#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _resolve_path(value: str | Path, project_root: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (project_root / path).resolve()
    return path


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fp:
        loaded = yaml.safe_load(fp)
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError(f"Invalid YAML format in {path}")
    return loaded


def _parse_date(value: str) -> pd.Timestamp:
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return pd.Timestamp.strptime(value, fmt)
        except Exception:
            pass
    return pd.Timestamp(value)


def _selection_date_from_name(path: Path) -> pd.Timestamp | None:
    m = re.match(r"selection_(\d{8})\.csv$", path.name)
    if not m:
        return None
    return pd.Timestamp.strptime(m.group(1), "%Y%m%d")


def _read_csv_with_fallback(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig")


def _read_selection(path: Path) -> pd.DataFrame:
    df = _read_csv_with_fallback(path)
    if df.empty:
        return df
    df.columns = [str(c).strip() for c in df.columns]
    required = {"instrument", "score"}
    if not required.issubset(set(df.columns)):
        raise ValueError(f"Missing columns {required} in {path}")
    df["instrument"] = df["instrument"].astype(str).str.strip()
    df["score"] = pd.to_numeric(df["score"], errors="coerce")
    df = df.dropna(subset=["instrument", "score"]).copy()
    if "signal_date" not in df.columns:
        dt = _selection_date_from_name(path)
        if dt is None:
            raise ValueError(f"selection_date not found in {path} and no signal_date column")
        df["signal_date"] = dt.strftime("%Y-%m-%d")
    return df


def _find_selection_file(selection_dir: Path, pattern: str, trade_date: str | None) -> Path:
    files = list(selection_dir.glob(pattern))
    if not files:
        raise FileNotFoundError(f"No files matched {pattern} in {selection_dir}")
    parsed = []
    for file in files:
        dt = _selection_date_from_name(file)
        if dt is None:
            continue
        parsed.append((dt, file))
    if not parsed:
        raise FileNotFoundError(f"selection file date parsing failed under {selection_dir} with pattern {pattern}")
    parsed = sorted(parsed, key=lambda item: item[0])
    if trade_date:
        target = _parse_date(trade_date).normalize()
        for dt, file in reversed(parsed):
            if dt.normalize() == target:
                return file
        raise FileNotFoundError(f"No selection file for trade_date {target.date()} under {selection_dir}")
    return parsed[-1][1]


def _find_previous_selection(selection_dir: Path, current_date: pd.Timestamp, pattern: str) -> Path | None:
    files = []
    for file in selection_dir.glob(pattern):
        dt = _selection_date_from_name(file)
        if dt is not None and dt.normalize() < current_date.normalize():
            files.append((dt, file))
    if not files:
        return None
    return sorted(files, key=lambda item: item[0])[-1][1]


@dataclass
class WeightSnapshot:
    signal_date: pd.Timestamp
    weights: pd.Series
    scores: pd.Series


def _cap_and_scale_weights(weights: pd.Series, max_position: float, target_total: float) -> pd.Series:
    if weights.empty or target_total <= 0:
        return weights
    weights = pd.Series(weights, dtype=float).copy()
    fixed = pd.Series(False, index=weights.index)
    remaining_budget = float(target_total)

    for _ in range(1000):
        active = ~fixed
        if not active.any() or remaining_budget <= 0:
            break

        active_sum = weights.loc[active].sum()
        if active_sum <= 0:
            share = remaining_budget / active.sum()
            equal = pd.Series(share, index=weights.loc[active].index)
            weights.loc[active] = equal
            break

        weights.loc[active] = weights.loc[active] / active_sum * remaining_budget
        over = (weights.loc[active] > max_position + 1e-12)
        if not over.any():
            break

        over_idx = over[over].index
        weights.loc[over_idx] = max_position
        fixed.loc[over_idx] = True
        remaining_budget -= float(max_position * len(over_idx))

    return weights.clip(lower=0.0)


def _build_weights(df: pd.DataFrame, top_n: int, min_score: float, max_position: float, max_exposure: float, min_position: float) -> WeightSnapshot:
    if df.empty:
        return WeightSnapshot(pd.Timestamp.min, pd.Series(dtype=float), pd.Series(dtype=float))

    latest = pd.to_datetime(df["signal_date"], errors="coerce").max()
    if pd.isna(latest):
        raise ValueError("Cannot parse signal_date in selection file")

    snap = df.copy()
    snap = snap.dropna(subset=["score", "instrument"])
    snap = snap.drop_duplicates(subset=["instrument"], keep="first")
    snap = snap[snap["score"] >= min_score].sort_values("score", ascending=False)
    if snap.empty:
        snap = df.drop_duplicates(subset=["instrument"], keep="first").sort_values("score", ascending=False).head(top_n)
    else:
        snap = snap.head(top_n)

    if snap.empty:
        return WeightSnapshot(latest, pd.Series(dtype=float), pd.Series(dtype=float))

    raw = snap["score"].clip(lower=0.0)
    if (raw <= 0).all():
        raw = pd.Series(range(len(raw), 0, -1), index=raw.index, dtype=float)
    raw = raw - raw.min() + 1e-12
    if raw.sum() <= 0:
        raw = pd.Series(1.0, index=raw.index)

    weights = _cap_and_scale_weights(raw, max_position=max_position, target_total=max_exposure)
    if min_position > 0:
        keep = weights >= min_position
        if keep.any():
            weights = weights[keep]
        else:
            weights = weights.copy()

    scores = snap.set_index("instrument")["score"]
    weights = weights.reindex(scores.index).fillna(0.0)
    weights = weights / weights.sum() * min(max_exposure, weights.sum()) if weights.sum() > 0 else weights
    return WeightSnapshot(latest.normalize(), weights, scores)


def _load_snapshot(path: Path, config: dict[str, Any]) -> WeightSnapshot:
    top_n = _as_int(config.get("top_n", 30), 30)
    min_score = _as_float(config.get("min_score", 0.0), 0.0)
    max_position = _as_float(config.get("max_position", 0.1), 0.1)
    max_exposure = _as_float(config.get("max_exposure", 1.0), 1.0)
    min_position = _as_float(config.get("min_position", 0.0), 0.0)

    if "target_weight" in _read_selection(path).columns:
        df = _read_selection(path)
        if df.empty:
            return WeightSnapshot(pd.Timestamp.min, pd.Series(dtype=float), pd.Series(dtype=float))
        df = df.copy()
        if "target_weight" not in df.columns:
            raise ValueError(f"File {path} has no target_weight column")
        weights = pd.to_numeric(df["target_weight"], errors="coerce").fillna(0.0)
        scores = pd.to_numeric(df.get("score", pd.Series(0.0, index=df.index)), errors="coerce").fillna(0.0)
        date = pd.to_datetime(df["signal_date"], errors="coerce").max()
        if pd.isna(date):
            date = _selection_date_from_name(path) or pd.Timestamp.today()
        return WeightSnapshot(date.normalize(), pd.Series(weights.values, index=df["instrument"], dtype=float), pd.Series(scores.values, index=df["instrument"]))

    df = _read_selection(path)
    return _build_weights(df, top_n=top_n, min_score=min_score, max_position=max_position, max_exposure=max_exposure, min_position=min_position)


def build_trade_plan(config_path: Path, selection_file: Path | None, selection_date: str | None, prev_selection_file: Path | None, allow_weight_update: bool) -> tuple[Path, pd.DataFrame]:
    project_root = Path(__file__).resolve().parents[1]
    cfg_all = _load_yaml(config_path)
    exec_cfg = cfg_all.get("execution", {})
    selection_dir = _resolve_path(exec_cfg.get("selection_dir", "./data/output"), project_root)
    selection_pattern = str(exec_cfg.get("selection_glob", "selection_*.csv"))
    output_dir = _resolve_path(exec_cfg.get("output_dir", "./data/output"), project_root)
    output_dir.mkdir(parents=True, exist_ok=True)

    top_n = _as_int(exec_cfg.get("top_n", 30), 30)
    min_score = _as_float(exec_cfg.get("min_score", 0.0), 0.0)
    rebalance_threshold = _as_float(exec_cfg.get("rebalance_threshold", 0.005), 0.005)
    max_position = _as_float(exec_cfg.get("max_position", 0.1), 0.1)
    max_exposure = _as_float(exec_cfg.get("max_exposure", 1.0), 1.0)
    min_position = _as_float(exec_cfg.get("min_position", 0.0), 0.0)
    allow_weight_update = allow_weight_update and _as_bool(exec_cfg.get("allow_weight_update", True), True)

    output_columns = exec_cfg.get(
        "output_columns",
        [
            "trade_date",
            "signal_date",
            "instrument",
            "action",
            "target_weight",
            "current_weight",
            "weight_delta",
            "score",
            "trigger",
            "topn_threshold",
            "min_score_threshold",
            "rebalance_threshold",
            "max_position",
            "max_exposure",
        ],
    )

    if selection_file is None:
        selection_file = _find_selection_file(selection_dir, selection_pattern, selection_date)
    else:
        selection_file = selection_file.resolve()
    print(f"[INFO] Selection source: {selection_file}")

    current = _read_selection(selection_file)
    current_snapshot = _build_weights(
        current,
        top_n=top_n,
        min_score=min_score,
        max_position=max_position,
        max_exposure=max_exposure,
        min_position=min_position,
    )
    signal_date = current_snapshot.signal_date

    previous = None
    if prev_selection_file is None:
        prev_path = _find_previous_selection(selection_dir, signal_date, selection_pattern)
        if prev_path is not None:
            previous = _load_snapshot(prev_path, exec_cfg)
            print(f"[INFO] Previous selection used for diff: {prev_path}")
    else:
        previous = _load_snapshot(prev_selection_file, exec_cfg)
        print(f"[INFO] Previous file used for diff: {prev_selection_file}")

    prev_weights = previous.weights if previous else pd.Series(dtype=float)
    target_map = current_snapshot.weights
    score_map = current_snapshot.scores

    all_instruments = sorted(set(target_map.index) | set(prev_weights.index))
    rows = []
    for instrument in all_instruments:
        target_w = float(target_map.get(instrument, 0.0))
        current_w = float(prev_weights.get(instrument, 0.0))
        score = float(score_map.get(instrument, 0.0))
        delta = target_w - current_w

        if target_w > 0 and current_w <= 0:
            action = "BUY"
            if score >= min_score and len(target_map.index) <= top_n:
                trigger = f"score>=min_score and within top{top_n}"
            else:
                trigger = "manual entry"
        elif target_w <= 0 and current_w > 0:
            action = "SELL"
            trigger = "removed_from_current_topn_or_below_threshold"
        elif target_w > 0 and current_w > 0 and allow_weight_update and abs(delta) >= rebalance_threshold:
            action = "WEIGHT"
            trigger = "weight_update"
        else:
            continue

        rows.append(
            {
                "trade_date": signal_date.strftime("%Y-%m-%d"),
                "signal_date": signal_date.strftime("%Y-%m-%d"),
                "instrument": instrument,
                "action": action,
                "target_weight": round(target_w, 6),
                "current_weight": round(current_w, 6),
                "weight_delta": round(delta, 6),
                "score": round(score, 6),
                "trigger": trigger,
                "topn_threshold": top_n,
                "min_score_threshold": min_score,
                "rebalance_threshold": rebalance_threshold,
                "max_position": max_position,
                "max_exposure": max_exposure,
            }
        )

    plan = pd.DataFrame(rows)
    if plan.empty:
        plan = pd.DataFrame(columns=output_columns)
    else:
        plan = plan.reindex(columns=output_columns)
        action_order = {"SELL": 0, "BUY": 1, "WEIGHT": 2}
        plan["action_sort"] = plan["action"].map(action_order).fillna(3)
        plan = plan.sort_values(["action_sort", "instrument"], ascending=[True, True]).drop(columns=["action_sort"]).reset_index(drop=True)

    out_file = output_dir / f"trade_plan_{signal_date.strftime('%Y%m%d')}.csv"
    plan.to_csv(out_file, index=False, encoding="utf-8-sig")
    return out_file, plan


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a trade plan from Qlib selection output.")
    parser.add_argument(
        "--config",
        default="configs/trading_execution_template.yaml",
        help="Path to execution config yaml.",
    )
    parser.add_argument(
        "--selection-date",
        help="Trade date to use for selection file name, format YYYYMMDD or YYYY-MM-DD.",
    )
    parser.add_argument("--selection-file", default=None, help="Explicit selection csv file path.")
    parser.add_argument("--prev-selection-file", default=None, help="Previous selection or trade plan csv for diff.")
    parser.add_argument(
        "--no-weight-update",
        action="store_true",
        help="Do not output WEIGHT rows when only weight adjustment is required.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    project_root = Path(__file__).resolve().parents[1]
    config_path = _resolve_path(args.config, project_root)
    selection_file = Path(args.selection_file).resolve() if args.selection_file else None
    prev_selection_file = Path(args.prev_selection_file).resolve() if args.prev_selection_file else None
    out_file, plan = build_trade_plan(
        config_path=config_path,
        selection_file=selection_file,
        selection_date=args.selection_date,
        prev_selection_file=prev_selection_file,
        allow_weight_update=not args.no_weight_update,
    )
    buys = int((plan["action"] == "BUY").sum()) if not plan.empty else 0
    sells = int((plan["action"] == "SELL").sum()) if not plan.empty else 0
    weights = int((plan["action"] == "WEIGHT").sum()) if not plan.empty else 0
    print(f"[INFO] Build plan complete: BUY={buys}, SELL={sells}, WEIGHT={weights}, file={out_file}")


if __name__ == "__main__":
    main()
