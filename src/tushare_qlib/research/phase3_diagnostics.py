from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..feature_store import load_feature_store
from ..lineage import git_revision, sha256_json
from ..platform_release import load_platform_release
from ..prediction_snapshot import load_prediction_snapshot
from ..settings import Settings
from ..store import sha256_file
from .feature_diagnostics import feature_columns
from .regime import build_regime_labels, load_regime_spec
from .regime_diagnostics import (
    ModelComparisonSpec,
    derive_model_regime_diagnostics,
)
from .regime_study import (
    _history_start,
    _load_benchmark_close,
    _load_pit_industries,
    _load_stock_returns,
)
from .phase3_contract import load_phase3_lock
from .phase3_decay import derive_model_age_decay
from .phase3_program import PHASE3_EXECUTION_ORDER, load_phase3_plan


PHASE3_DIAGNOSTICS_SCHEMA = "phase3_diagnostics_v1"
PHASE3_EVIDENCE_INDEX_SCHEMA = "phase3_evidence_index_v1"
PHASE3_MANIFEST_NAME = "phase3_evidence_index.json"


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return value


def _load_json(path: Path, name: str) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"{name} is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{name} must be a JSON object")
    return payload


def _safe_corr(left: pd.Series, right: pd.Series, method: str) -> float:
    paired = pd.concat([left, right], axis=1).dropna()
    if len(paired) < 2 or paired.iloc[:, 0].nunique() < 2 or paired.iloc[:, 1].nunique() < 2:
        return float("nan")
    return float(paired.iloc[:, 0].corr(paired.iloc[:, 1], method=method))


def _topk_members(block: pd.DataFrame, topk: int) -> tuple[set[str], set[str]]:
    eligible = block[["score", "label"]].dropna().sort_index(kind="stable")
    if len(eligible) < topk * 2:
        return set(), set()
    top = eligible["score"].nlargest(topk, keep="first").index.astype(str)
    bottom = eligible["score"].nsmallest(topk, keep="first").index.astype(str)
    return set(top), set(bottom)


def derive_daily_stability_metrics(
    predictions: Mapping[str, pd.DataFrame],
    *,
    topk: int,
    minimum_cross_section: int,
    model_comparisons: Sequence[ModelComparisonSpec] = (),
    portfolio_excess: Mapping[str, pd.Series] | None = None,
) -> pd.DataFrame:
    if not predictions:
        raise ValueError("at least one anchor PredictionSnapshot is required")
    rows: list[dict[str, object]] = []
    by_model: dict[str, pd.DataFrame] = {}
    for model, raw in sorted(predictions.items()):
        if not isinstance(raw.index, pd.MultiIndex) or raw.index.names != ["datetime", "instrument"]:
            raise ValueError(f"{model} predictions require a datetime/instrument MultiIndex")
        if raw.index.has_duplicates or not {"score", "label"}.issubset(raw):
            raise ValueError(f"{model} predictions require unique score/label rows")
        previous_top: set[str] | None = None
        model_rows: list[dict[str, object]] = []
        excess = portfolio_excess.get(model) if portfolio_excess is not None else None
        for date, raw_block in raw.sort_index().groupby(level="datetime", sort=True):
            block = raw_block.droplevel("datetime")
            clean = block[["score", "label"]].apply(pd.to_numeric, errors="coerce").dropna()
            eligible = len(clean) >= minimum_cross_section
            top, bottom = _topk_members(clean, topk)
            topk_spread = (
                float(clean.loc[list(top), "label"].mean() - clean.loc[list(bottom), "label"].mean())
                if top and bottom
                else float("nan")
            )
            turnover = (
                float(1.0 - len(top & previous_top) / topk)
                if top and previous_top is not None
                else float("nan")
            )
            if top:
                previous_top = top
            normalized_date = pd.Timestamp(date).normalize()
            portfolio_value = (
                float(pd.to_numeric(excess.reindex([normalized_date]), errors="coerce").iloc[0])
                if excess is not None and normalized_date in excess.index
                else float("nan")
            )
            model_rows.append(
                {
                    "date": normalized_date,
                    "model": model,
                    "metric_role": "anchor",
                    "valid_count": len(clean),
                    "ic": _safe_corr(clean["score"], clean["label"], "pearson") if eligible else float("nan"),
                    "rank_ic": _safe_corr(clean["score"], clean["label"], "spearman")
                    if eligible
                    else float("nan"),
                    "topk_spread": topk_spread,
                    "turnover": turnover,
                    "portfolio_excess_return": portfolio_value,
                    "portfolio_metric_status": (
                        "AVAILABLE" if np.isfinite(portfolio_value) else "INPUT_UNAVAILABLE"
                    ),
                }
            )
        by_model[model] = pd.DataFrame(model_rows).set_index("date")
        rows.extend(model_rows)
    for comparison in model_comparisons:
        if comparison.candidate not in by_model or comparison.baseline not in by_model:
            raise ValueError(f"comparison {comparison.comparison_id} references an unknown anchor")
        candidate = by_model[comparison.candidate]
        baseline = by_model[comparison.baseline]
        if not candidate.index.equals(baseline.index):
            raise ValueError(f"comparison {comparison.comparison_id} dates do not align")
        delta = pd.DataFrame(
            {
                "date": candidate.index,
                "model": comparison.comparison_id,
                "metric_role": "descriptive_comparison",
                "valid_count": np.minimum(candidate["valid_count"], baseline["valid_count"]),
                "ic": candidate["ic"] - baseline["ic"],
                "rank_ic": candidate["rank_ic"] - baseline["rank_ic"],
                "topk_spread": candidate["topk_spread"] - baseline["topk_spread"],
                "turnover": candidate["turnover"] - baseline["turnover"],
                "portfolio_excess_return": (
                    candidate["portfolio_excess_return"] - baseline["portfolio_excess_return"]
                ),
                "portfolio_metric_status": np.where(
                    candidate["portfolio_excess_return"].notna()
                    & baseline["portfolio_excess_return"].notna(),
                    "AVAILABLE",
                    "INPUT_UNAVAILABLE",
                ),
            }
        )
        rows.extend(delta.to_dict(orient="records"))
    return pd.DataFrame(rows).sort_values(["date", "model"], kind="stable").reset_index(drop=True)


def derive_rolling_stability_metrics(
    daily_metrics: pd.DataFrame, windows: Sequence[int]
) -> dict[int, pd.DataFrame]:
    required = {"date", "model", "rank_ic", "topk_spread", "turnover"}
    if missing := required - set(daily_metrics):
        raise ValueError(f"daily metrics are missing columns: {sorted(missing)}")
    output: dict[int, pd.DataFrame] = {}
    for raw_window in windows:
        window = int(raw_window)
        if window < 2:
            raise ValueError("rolling diagnostic windows must be at least two sessions")
        rows: list[pd.DataFrame] = []
        for model, block in daily_metrics.groupby("model", sort=True):
            ordered = block.sort_values("date", kind="stable").copy()
            rank_ic = pd.to_numeric(ordered["rank_ic"], errors="coerce")
            ordered["window"] = window
            ordered["rolling_rank_ic"] = rank_ic.rolling(window, min_periods=window).mean()
            ordered["rolling_hit_ratio"] = rank_ic.gt(0).rolling(window, min_periods=window).mean()
            ordered["rolling_topk_spread"] = (
                pd.to_numeric(ordered["topk_spread"], errors="coerce")
                .rolling(window, min_periods=window)
                .mean()
            )
            ordered["rolling_turnover"] = (
                pd.to_numeric(ordered["turnover"], errors="coerce").rolling(window, min_periods=window).mean()
            )
            ordered["rolling_portfolio_excess_return"] = (
                pd.to_numeric(ordered["portfolio_excess_return"], errors="coerce")
                .rolling(window, min_periods=window)
                .mean()
            )
            rows.append(
                ordered[
                    [
                        "date",
                        "model",
                        "metric_role",
                        "window",
                        "rolling_rank_ic",
                        "rolling_hit_ratio",
                        "rolling_topk_spread",
                        "rolling_turnover",
                        "rolling_portfolio_excess_return",
                    ]
                ].dropna(subset=["rolling_rank_ic"])
            )
        output[window] = pd.concat(rows, ignore_index=True).sort_values(["date", "model"], kind="stable")
    return output


def _regime_context(labels: pd.DataFrame, dates: pd.DatetimeIndex) -> tuple[str, str, int]:
    selected = labels.loc[labels["date"].isin(dates) & labels["status"].eq("AVAILABLE")]
    if selected.empty:
        return "{}", "{}", 0
    start_date = dates.min()
    at_start = {
        str(row.dimension): str(row.state)
        for row in selected.loc[selected["date"].eq(start_date)].itertuples(index=False)
    }
    majority: dict[str, str] = {}
    for dimension, block in selected.groupby("dimension", sort=True):
        counts = block["state"].astype(str).value_counts()
        majority[str(dimension)] = str(sorted(counts[counts.eq(counts.max())].index)[0])
    return (
        json.dumps(at_start, ensure_ascii=False, sort_keys=True),
        json.dumps(majority, ensure_ascii=False, sort_keys=True),
        int(selected["transition"].fillna(False).sum()) if "transition" in selected else 0,
    )


def derive_failure_windows(
    rolling_metrics: Mapping[int, pd.DataFrame],
    daily_metrics: pd.DataFrame,
    regime_labels: pd.DataFrame,
) -> pd.DataFrame:
    daily_by_model = {
        str(model): block.set_index("date").sort_index()
        for model, block in daily_metrics.groupby("model", sort=True)
    }
    rows: list[dict[str, object]] = []
    for window, frame in sorted(rolling_metrics.items()):
        for model, block in frame.groupby("model", sort=True):
            ordered = block.sort_values("date", kind="stable").copy()
            failed = ordered["rolling_rank_ic"].lt(0)
            episode = failed.ne(failed.shift(fill_value=False)).cumsum()
            for _, failure in ordered.loc[failed].groupby(episode.loc[failed], sort=True):
                dates = pd.DatetimeIndex(failure["date"]).normalize()
                daily = daily_by_model[str(model)].reindex(dates)
                portfolio = pd.to_numeric(daily["portfolio_excess_return"], errors="coerce")
                spread = pd.to_numeric(daily["topk_spread"], errors="coerce")
                if portfolio.notna().all() and len(portfolio):
                    cumulative = float((1.0 + portfolio).prod() - 1.0)
                    excess_source = "portfolio_excess_return"
                else:
                    cumulative = float(spread.sum())
                    excess_source = "topk_forward_label_spread_proxy"
                regime_at_start, regime_majority, transition_count = _regime_context(regime_labels, dates)
                rows.append(
                    {
                        "model": model,
                        "window": int(window),
                        "start_date": dates.min(),
                        "end_date": dates.max(),
                        "sessions": len(dates),
                        "mean_rank_ic": float(failure["rolling_rank_ic"].mean()),
                        "min_rank_ic": float(failure["rolling_rank_ic"].min()),
                        "cumulative_excess": cumulative,
                        "excess_source": excess_source,
                        "regime_at_start": regime_at_start,
                        "regime_majority": regime_majority,
                        "transition_count": transition_count,
                    }
                )
    columns = [
        "model",
        "window",
        "start_date",
        "end_date",
        "sessions",
        "mean_rank_ic",
        "min_rank_ic",
        "cumulative_excess",
        "excess_source",
        "regime_at_start",
        "regime_majority",
        "transition_count",
    ]
    return (
        pd.DataFrame(rows, columns=columns)
        .sort_values(["window", "model", "start_date"], kind="stable")
        .reset_index(drop=True)
    )


def derive_regime_transition_metrics(
    daily_metrics: pd.DataFrame,
    regime_labels: pd.DataFrame,
    *,
    windows: Sequence[int],
) -> pd.DataFrame:
    available = regime_labels.loc[regime_labels["status"].eq("AVAILABLE")].copy()
    available["date"] = pd.to_datetime(available["date"], errors="raise").dt.normalize()
    events: list[dict[str, object]] = []
    metrics = ("rank_ic", "topk_spread", "turnover", "portfolio_excess_return")
    for dimension, labels in available.groupby("dimension", sort=True):
        labels = labels.sort_values("date", kind="stable").reset_index(drop=True)
        labels["from_state"] = labels["state"].shift(1)
        transitions = labels.loc[labels["transition"].fillna(False) & labels["from_state"].notna()]
        date_list = pd.DatetimeIndex(labels["date"])
        positions = {date: position for position, date in enumerate(date_list)}
        for transition in transitions.itertuples(index=False):
            transition_date = pd.Timestamp(transition.date).normalize()
            position = positions[transition_date]
            for raw_window in windows:
                window = int(raw_window)
                before_dates = date_list[max(0, position - window) : position]
                after_dates = date_list[position : min(len(date_list), position + window)]
                for model, model_daily in daily_metrics.groupby("model", sort=True):
                    indexed = model_daily.set_index("date")
                    before = indexed.reindex(before_dates)
                    after = indexed.reindex(after_dates)
                    row: dict[str, object] = {
                        "model": model,
                        "dimension": dimension,
                        "from_state": str(transition.from_state),
                        "to_state": str(transition.state),
                        "transition_date": transition_date,
                        "window": window,
                        "before_sessions": int(before["rank_ic"].notna().sum()),
                        "after_sessions": int(after["rank_ic"].notna().sum()),
                    }
                    for metric in metrics:
                        before_value = float(pd.to_numeric(before[metric], errors="coerce").mean())
                        after_value = float(pd.to_numeric(after[metric], errors="coerce").mean())
                        row[f"before_{metric}"] = before_value
                        row[f"after_{metric}"] = after_value
                        row[f"delta_{metric}"] = after_value - before_value
                    row["before_failure_probability"] = float(
                        pd.to_numeric(before["rank_ic"], errors="coerce").dropna().lt(0).mean()
                    )
                    row["after_failure_probability"] = float(
                        pd.to_numeric(after["rank_ic"], errors="coerce").dropna().lt(0).mean()
                    )
                    events.append(row)
    event_frame = pd.DataFrame(events)
    if event_frame.empty:
        return pd.DataFrame(columns=["model", "dimension", "from_state", "to_state", "window", "event_count"])
    aggregate_columns = [
        column
        for column in event_frame
        if column.startswith(("before_", "after_", "delta_"))
        and column.endswith(("rank_ic", "topk_spread", "turnover", "portfolio_excess_return", "probability"))
    ]
    grouped = event_frame.groupby(["model", "dimension", "from_state", "to_state", "window"], sort=True)
    result = grouped[aggregate_columns].mean().reset_index()
    result["event_count"] = grouped["transition_date"].nunique().to_numpy()
    result["minimum_before_sessions"] = grouped["before_sessions"].min().to_numpy()
    result["minimum_after_sessions"] = grouped["after_sessions"].min().to_numpy()
    return result.sort_values(
        ["dimension", "from_state", "to_state", "window", "model"], kind="stable"
    ).reset_index(drop=True)


def _fold_calendar(anchor_runs: Mapping[str, Any]) -> list[dict[str, object]]:
    calendars: list[tuple[tuple[str, str, str, str | None], ...]] = []
    for anchor in anchor_runs.values():
        windows: set[tuple[str, str, str, str | None]] = set()
        for run_info in anchor["runs"]:
            run = _load_json(Path(run_info["path"]), "anchor run manifest")
            for raw in run.get("folds", ()):
                fold = _mapping(raw, "anchor fold")
                test = fold.get("test")
                if not isinstance(test, Sequence) or isinstance(test, (str, bytes)) or len(test) != 2:
                    raise ValueError("anchor fold test window requires start/end")
                train = fold.get("train")
                train_end = (
                    str(pd.Timestamp(train[1]).normalize().date())
                    if isinstance(train, Sequence) and not isinstance(train, (str, bytes)) and len(train) == 2
                    else None
                )
                start = str(pd.Timestamp(test[0]).normalize().date())
                end = str(pd.Timestamp(test[1]).normalize().date())
                key = str(fold.get("key") or "fold")
                windows.add((key, start, end, train_end))
        calendar = tuple(sorted(windows, key=lambda item: (item[1], item[2], item[0])))
        if not calendar:
            raise ValueError("anchor evidence contains no rolling-OOS fold calendar")
        calendars.append(calendar)
    if any(value != calendars[0] for value in calendars[1:]):
        raise ValueError("Phase 3 anchor fold calendars drift")
    return [
        {"foldId": key, "start": start, "end": end, "trainEnd": train_end}
        for key, start, end, train_end in calendars[0]
    ]


def _load_anchor_predictions(lock: Mapping[str, Any]) -> dict[str, pd.DataFrame]:
    locked_anchors = _mapping(_mapping(lock.get("lineage"), "lineage").get("anchors"), "anchors")
    result: dict[str, pd.DataFrame] = {}
    for anchor_id, raw in locked_anchors.items():
        anchor = _mapping(raw, f"anchor {anchor_id}")
        frames: list[pd.DataFrame] = []
        for run_value in anchor.get("runs", ()):
            run = _mapping(run_value, "anchor run lineage")
            run_path = Path(str(run.get("path") or "")).resolve()
            if not run_path.is_file() or sha256_file(run_path) != run.get("sha256"):
                raise ValueError(f"anchor run manifest checksum mismatch: {run_path}")
            snapshot = _mapping(run.get("predictionSnapshot"), "anchor PredictionSnapshot lineage")
            snapshot_path = Path(str(snapshot.get("path") or "")).resolve()
            if not snapshot_path.is_file() or sha256_file(snapshot_path) != snapshot.get("sha256"):
                raise ValueError(f"anchor PredictionSnapshot checksum mismatch: {snapshot_path}")
            frame, manifest = load_prediction_snapshot(snapshot_path)
            if manifest.get("snapshotId") != snapshot.get("snapshotId"):
                raise ValueError(f"anchor PredictionSnapshot identity drift: {anchor_id}")
            frames.append(frame)
        combined = pd.concat(frames).sort_index()
        if combined.index.has_duplicates:
            raise ValueError(f"anchor PredictionSnapshots overlap: {anchor_id}")
        result[str(anchor_id)] = combined
    reference = next(iter(result.values()))
    for anchor_id, frame in result.items():
        if not frame.index.equals(reference.index):
            raise ValueError(f"anchor PredictionSnapshot keys drift: {anchor_id}")
        try:
            pd.testing.assert_series_equal(
                pd.to_numeric(frame["label"], errors="coerce"),
                pd.to_numeric(reference["label"], errors="coerce"),
                check_dtype=False,
                check_names=False,
            )
        except AssertionError as exc:
            raise ValueError(f"anchor PredictionSnapshot labels drift: {anchor_id}") from exc
    return result


def _fold_assignments(
    dates: pd.DatetimeIndex, fold_calendar: Sequence[Mapping[str, object]]
) -> dict[pd.Timestamp, str]:
    result: dict[pd.Timestamp, str] = {}
    for fold in fold_calendar:
        start = pd.Timestamp(fold["start"]).normalize()
        end = pd.Timestamp(fold["end"]).normalize()
        for date in dates[(dates >= start) & (dates <= end)]:
            normalized = pd.Timestamp(date).normalize()
            if normalized in result:
                raise ValueError("Phase 3 fold calendar overlaps")
            result[normalized] = str(fold["foldId"])
    if set(result) != set(dates):
        raise ValueError("Phase 3 fold calendar does not cover every anchor date")
    return result


def _artifact(path: Path, rows: int | None = None) -> dict[str, object]:
    result: dict[str, object] = {"name": path.name, "path": path.name, "sha256": sha256_file(path)}
    if rows is not None:
        result["rows"] = rows
    return result


def _json_safe(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (float, np.floating)) and not np.isfinite(value):
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    return value


def _summary(
    daily: pd.DataFrame,
    rolling: Mapping[int, pd.DataFrame],
    regime: pd.DataFrame,
    transitions: pd.DataFrame,
    decay: pd.DataFrame,
) -> dict[str, Any]:
    models: dict[str, Any] = {}
    for model, block in daily.groupby("model", sort=True):
        model_summary: dict[str, object] = {
            "meanRankIc": float(pd.to_numeric(block["rank_ic"], errors="coerce").mean()),
            "positiveDayRatio": float(pd.to_numeric(block["rank_ic"], errors="coerce").dropna().gt(0).mean()),
            "portfolioMetricStatus": (
                "AVAILABLE" if block["portfolio_metric_status"].eq("AVAILABLE").all() else "INPUT_UNAVAILABLE"
            ),
        }
        for window, frame in rolling.items():
            values = pd.to_numeric(
                frame.loc[frame["model"].eq(model), "rolling_rank_ic"], errors="coerce"
            ).dropna()
            model_summary[f"rolling{window}"] = {
                "minimum": float(values.min()) if len(values) else float("nan"),
                "p05": float(values.quantile(0.05)) if len(values) else float("nan"),
                "failureWindowRatio": float(values.lt(0).mean()) if len(values) else float("nan"),
            }
        models[str(model)] = model_summary
    return cast_dict(
        _json_safe(
            {
                "models": models,
                "regimeRows": len(regime),
                "transitionRows": len(transitions),
                "decayRows": len(decay),
                "diagnosticOnly": True,
                "formalCandidatesCreated": 0,
                "selectionUsesFinalHoldout": False,
                "publishingAuthorized": False,
            }
        )
    )


def cast_dict(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("expected a dictionary")
    return value


def _write_report(path: Path, summary: Mapping[str, Any]) -> None:
    lines = [
        "# Alpha Research Phase 3-D — Stability Diagnosis",
        "",
        "- State: PHASE3_DIAGNOSIS_COMPLETE",
        "- Diagnosis only: true",
        "- Formal candidates created: 0",
        "- Final holdout accessed: false",
        "- Publishing authorized: false",
        "",
        "## Anchor and comparison stability",
        "",
        "| Model | Mean RankIC | Rolling window | Minimum | P05 | Failure ratio | Portfolio metric |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    models = _mapping(summary.get("models"), "summary models")
    for model, raw in models.items():
        item = _mapping(raw, "model summary")
        rolling_keys = sorted(
            (key for key in item if str(key).startswith("rolling")),
            key=lambda key: int(str(key).removeprefix("rolling")),
        )
        for key in rolling_keys:
            values = _mapping(item[key], "rolling summary")
            lines.append(
                f"| {model} | {item['meanRankIc']:.6f} | {str(key).removeprefix('rolling')} | "
                f"{values['minimum'] if values['minimum'] is not None else 'n/a'} | "
                f"{values['p05'] if values['p05'] is not None else 'n/a'} | "
                f"{values['failureWindowRatio'] if values['failureWindowRatio'] is not None else 'n/a'} | "
                f"{item['portfolioMetricStatus']} |"
            )
    lines.extend(
        [
            "",
            "`topk_spread` is a forward-label diagnostic proxy, not a realized portfolio return. "
            "`portfolio_excess_return` remains unavailable because Phase 3-D prohibits external portfolio evidence.",
            "",
            "Regime tables are descriptive discovery evidence. They do not define or approve a Phase 3-C hypothesis.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _expected_artifact_names(lock: Mapping[str, Any]) -> set[str]:
    diagnostics = _mapping(_mapping(lock.get("contract"), "contract").get("diagnostics"), "diagnostics")
    return {
        "daily_model_metrics.parquet",
        "failure_windows.parquet",
        "regime_labels.parquet",
        "regime_model_metrics.parquet",
        "regime_transition_metrics.parquet",
        "training_age_decay.parquet",
        "anchor_predictions_index.json",
        "phase3_diagnostics_report.json",
        "phase3_diagnostics_report.md",
        *(f"rolling_{int(window)}_rank_ic.parquet" for window in diagnostics["rolling_windows"]),
    }


def _validate_existing(
    root: Path,
    *,
    lock: Mapping[str, Any],
    lock_path: Path,
    plan: Mapping[str, Any],
    plan_path: Path,
) -> Path:
    if not root.is_dir():
        raise ValueError("existing Phase 3 output is not a directory")
    manifest_path = root / PHASE3_MANIFEST_NAME
    manifest = _load_json(manifest_path, "existing Phase 3 evidence index")
    recorded_evidence_sha = str(manifest.get("evidenceSha256") or "")
    actual_evidence_sha = sha256_json(
        {key: value for key, value in manifest.items() if key != "evidenceSha256"}
    )
    if recorded_evidence_sha != actual_evidence_sha:
        raise ValueError("existing Phase 3 evidence-index checksum mismatch")
    if (
        manifest.get("schemaVersion") != PHASE3_EVIDENCE_INDEX_SCHEMA
        or manifest.get("programId") != lock.get("programId")
        or manifest.get("contractLockSha256") != lock.get("lockSha256")
        or manifest.get("studyType") != "ALPHA_STABILITY_REGIME_RESEARCH_DIAGNOSIS_ONLY"
    ):
        raise ValueError("existing Phase 3 diagnosis uses a different design lock")
    contract_binding = _mapping(manifest.get("contractLock"), "Phase 3 contract-lock binding")
    if contract_binding.get("sha256") != sha256_file(lock_path) or contract_binding.get(
        "lockSha256"
    ) != lock.get("lockSha256"):
        raise ValueError("existing Phase 3 contract-lock binding mismatch")
    plan_binding = _mapping(manifest.get("diagnosticPlan"), "Phase 3 diagnostic-plan binding")
    if plan_binding.get("sha256") != sha256_file(plan_path) or plan_binding.get("planSha256") != plan.get(
        "planSha256"
    ):
        raise ValueError("existing Phase 3 diagnostic-plan binding mismatch")
    if (
        manifest.get("state") != "PHASE3_DIAGNOSIS_COMPLETE"
        or tuple(manifest.get("completedWorkstreams", ())) != PHASE3_EXECUTION_ORDER
        or manifest.get("diagnosisOnly") is not True
        or manifest.get("formalCandidates") != []
        or manifest.get("formalCandidateCount") != 0
        or manifest.get("confirmationState") != "NOT_STARTED"
        or manifest.get("finalHoldoutAccessed") is not False
        or manifest.get("selectionUsesFinalHoldout") is not False
        or manifest.get("publishingAuthorized") is not False
    ):
        raise ValueError("existing Phase 3 diagnosis isolation state drift")
    locked_entry = _mapping(lock.get("entryCondition"), "entry condition")
    if manifest.get("phase2Evidence") != locked_entry.get("phase2Evidence"):
        raise ValueError("existing Phase 3 Phase 2 evidence binding mismatch")
    locked_lineage = _mapping(lock.get("lineage"), "design-lock lineage")
    locked_release = _mapping(locked_lineage.get("dataRelease"), "locked DataRelease")
    locked_feature = _mapping(locked_lineage.get("featureSnapshot"), "locked FeatureSnapshot")
    locked_regime = _mapping(locked_lineage.get("regimeSpec"), "locked regime spec")
    expected_lineage = {
        "dataReleaseId": locked_release.get("dataReleaseId"),
        "dataReleaseManifestSha256": locked_release.get("manifestSha256"),
        "datasetVersionId": locked_lineage.get("datasetVersionId"),
        "featureSnapshotId": locked_feature.get("featureSnapshotId"),
        "regimeSemanticSha256": locked_regime.get("semanticSha256"),
        "sourceCodeCommit": locked_lineage.get("sourceCodeCommit"),
        "sourceCodeDirty": False,
    }
    if manifest.get("lineage") != expected_lineage:
        raise ValueError("existing Phase 3 lineage drift")
    raw_artifacts = manifest.get("artifacts")
    if not isinstance(raw_artifacts, list):
        raise ValueError("existing Phase 3 artifact index must be a list")
    artifacts = [_mapping(raw, "Phase 3 artifact") for raw in raw_artifacts]
    names = [str(item.get("name") or "") for item in artifacts]
    paths = [str(item.get("path") or "") for item in artifacts]
    expected = _expected_artifact_names(lock)
    if (
        set(names) != expected
        or set(paths) != expected
        or len(names) != len(expected)
        or any(name != path for name, path in zip(names, paths, strict=True))
    ):
        raise ValueError("existing Phase 3 artifact set is incomplete or unexpected")
    for artifact in artifacts:
        target = (root / str(artifact.get("path") or "")).resolve()
        if target.parent != root or not target.is_file() or sha256_file(target) != artifact.get("sha256"):
            raise ValueError(f"existing Phase 3 artifact checksum mismatch: {target}")
        if target.suffix == ".parquet" and int(artifact.get("rows", -1)) != len(pd.read_parquet(target)):
            raise ValueError(f"existing Phase 3 artifact row-count mismatch: {target}")
    anchor_index = _load_json(root / "anchor_predictions_index.json", "anchor predictions index")
    if (
        anchor_index.get("schemaVersion") != PHASE3_DIAGNOSTICS_SCHEMA
        or anchor_index.get("anchors") != locked_lineage.get("anchors")
        or anchor_index.get("finalHoldout") is not False
        or anchor_index.get("publishingAuthorized") is not False
    ):
        raise ValueError("existing Phase 3 anchor index state drift")
    summary = _load_json(root / "phase3_diagnostics_report.json", "Phase 3 diagnostics summary")
    if summary != manifest.get("summary"):
        raise ValueError("existing Phase 3 summary differs from the evidence index")
    return manifest_path


def run_phase3_diagnose(
    settings: Settings,
    *,
    contract_lock: str | Path,
    plan_path: str | Path,
    evidence_index: str | Path,
    regime_path: str | Path,
    output_root: str | Path,
) -> Path:
    lock_path = Path(contract_lock).expanduser().resolve()
    plan_source = Path(plan_path).expanduser().resolve()
    evidence_path = Path(evidence_index).expanduser().resolve()
    regime_source = Path(regime_path).expanduser().resolve()
    lock = load_phase3_lock(lock_path)
    plan = load_phase3_plan(plan_source, contract_lock_sha256=str(lock["lockSha256"]))
    plan_lock = _mapping(plan.get("contractLock"), "diagnostic-plan contract lock")
    if plan.get("programId") != lock.get("programId") or plan_lock.get("sha256") != sha256_file(lock_path):
        raise ValueError("Phase 3 diagnostic plan does not bind the supplied design-lock file")
    locked_evidence = _mapping(
        _mapping(lock["entryCondition"], "entry condition").get("phase2Evidence"),
        "locked Phase 2 evidence",
    )
    if sha256_file(evidence_path) != locked_evidence.get("sha256"):
        raise ValueError("Phase 2 evidence index differs from the Phase 3 design lock")
    locked_regime = _mapping(_mapping(lock["lineage"], "lineage").get("regimeSpec"), "regime")
    spec = load_regime_spec(regime_source)
    if spec.file_sha256 != locked_regime.get("fileSha256") or spec.semantic_sha256 != locked_regime.get(
        "semanticSha256"
    ):
        raise ValueError("regime spec differs from the Phase 3 design lock")
    revision = git_revision(Path(__file__).resolve().parents[3])
    if not revision.get("commit") or revision.get("dirty") is not False:
        raise RuntimeError("Phase 3 diagnosis requires a clean committed source-code revision")
    if revision.get("commit") != _mapping(lock["lineage"], "lineage").get("sourceCodeCommit"):
        raise ValueError("Phase 3 diagnosis source-code commit differs from the design lock")
    implementation_root = Path(__file__).resolve().parent
    for name, expected in _mapping(
        _mapping(lock["lineage"], "lineage").get("implementationSha256"), "implementation hashes"
    ).items():
        target = implementation_root / str(name)
        if not target.is_file() or sha256_file(target) != expected:
            raise ValueError(f"Phase 3 implementation drift: {name}")

    predictions = _load_anchor_predictions(lock)
    contract = _mapping(lock["contract"], "Phase 3 contract")
    anchors = [dict(_mapping(value, "anchor")) for value in contract["anchors"]]
    comparisons = [
        ModelComparisonSpec(
            candidate=str(_mapping(value, "comparison")["candidate"]),
            baseline=str(_mapping(value, "comparison")["baseline"]),
        )
        for value in contract["comparisons"]
    ]
    diagnostics = _mapping(contract.get("diagnostics"), "diagnostics")
    portfolio_excess: dict[str, pd.Series] = {}
    daily = derive_daily_stability_metrics(
        predictions,
        topk=int(diagnostics["topk"]),
        minimum_cross_section=int(diagnostics["minimum_cross_section"]),
        model_comparisons=comparisons,
        portfolio_excess=portfolio_excess,
    )
    rolling = derive_rolling_stability_metrics(daily, diagnostics["rolling_windows"])

    release = load_platform_release(settings)
    locked_release = _mapping(_mapping(lock["lineage"], "lineage").get("dataRelease"), "release")
    if release.data_release_id != locked_release.get(
        "dataReleaseId"
    ) or release.manifest_sha256 != locked_release.get("manifestSha256"):
        raise ValueError("configured DataRelease differs from the Phase 3 design lock")
    evaluation_dates = pd.DatetimeIndex(daily["date"].unique()).normalize().sort_values()
    benchmark_close = _load_benchmark_close(release)
    history_start = _history_start(benchmark_close, evaluation_dates, spec)
    locked_feature = _mapping(_mapping(lock["lineage"], "lineage").get("featureSnapshot"), "FeatureSnapshot")
    feature_manifest_path = Path(str(locked_feature["path"])).resolve()
    if sha256_file(feature_manifest_path) != locked_feature.get("sha256"):
        raise ValueError("FeatureSnapshot manifest checksum differs from the Phase 3 design lock")
    feature_manifest = _load_json(feature_manifest_path, "FeatureSnapshot manifest")
    coverage = _mapping(feature_manifest.get("coverage"), "FeatureSnapshot coverage")
    features = feature_columns(
        load_feature_store(
            feature_manifest_path.parent,
            str(coverage.get("startTime")),
            str(evaluation_dates.max().date()),
            verify_checksums=True,
        )
    )
    required_features = {"TURNOVER_F", "LOG_CIRC_MV"}
    if missing := required_features - set(features):
        raise ValueError(f"FeatureSnapshot is missing Phase 3 regime fields: {sorted(missing)}")
    feature_dates = pd.DatetimeIndex(features.index.get_level_values("datetime")).normalize()
    regime_features = features.loc[
        (feature_dates >= history_start) & (feature_dates <= evaluation_dates.max()),
        sorted(required_features),
    ]
    reference = next(iter(predictions.values()))
    instruments = reference.index.get_level_values("instrument").unique()
    stock_returns = _load_stock_returns(
        release, instruments=instruments, start=history_start, end=evaluation_dates.max()
    )
    industries = _load_pit_industries(release, stock_returns.index)
    regime_labels = build_regime_labels(
        spec,
        benchmark_close=benchmark_close,
        features=regime_features,
        stock_returns=stock_returns,
        industries=industries,
        evaluation_dates=evaluation_dates,
    )
    fold_calendar = _fold_calendar(_mapping(_mapping(lock["lineage"], "lineage")["anchors"], "anchors"))
    fold_assignments = _fold_assignments(evaluation_dates, fold_calendar)
    regime_metrics = derive_model_regime_diagnostics(
        daily[["date", "model", "valid_count", "ic", "rank_ic"]],
        regime_labels,
        spec,
        fold_assignments,
    )
    transitions = derive_regime_transition_metrics(
        daily, regime_labels, windows=diagnostics["transition_windows"]
    )
    anchor_ids = {str(anchor["anchor_id"]) for anchor in anchors}
    decay = derive_model_age_decay(
        daily.loc[daily["model"].isin(anchor_ids)],
        fold_calendar,
        age_bucket_upper_sessions=diagnostics["age_bucket_upper_sessions"],
        hac_lag=spec.hac_lag,
    )
    failures = derive_failure_windows(rolling, daily, regime_labels)
    summary = _summary(daily, rolling, regime_metrics, transitions, decay)

    output = Path(output_root).expanduser().resolve()
    if output.exists():
        return _validate_existing(output, lock=lock, lock_path=lock_path, plan=plan, plan_path=plan_source)
    output.parent.mkdir(parents=True, exist_ok=True)
    building = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        frames: dict[str, pd.DataFrame] = {
            "daily_model_metrics.parquet": daily,
            "failure_windows.parquet": failures,
            "regime_labels.parquet": regime_labels,
            "regime_model_metrics.parquet": regime_metrics,
            "regime_transition_metrics.parquet": transitions,
            "training_age_decay.parquet": decay,
        }
        for window, frame in rolling.items():
            frames[f"rolling_{window}_rank_ic.parquet"] = frame
        artifacts: list[dict[str, object]] = []
        for name, frame in frames.items():
            target = building / name
            frame.to_parquet(target, index=False)
            artifacts.append(_artifact(target, len(frame)))
        anchor_index_path = building / "anchor_predictions_index.json"
        anchor_index_path.write_text(
            json.dumps(
                {
                    "schemaVersion": PHASE3_DIAGNOSTICS_SCHEMA,
                    "anchors": _mapping(_mapping(lock["lineage"], "lineage")["anchors"], "anchors"),
                    "foldCalendar": fold_calendar,
                    "finalHoldout": False,
                    "publishingAuthorized": False,
                },
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            ),
            encoding="utf-8",
        )
        artifacts.append(_artifact(anchor_index_path))
        summary_path = building / "phase3_diagnostics_report.json"
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
        )
        artifacts.append(_artifact(summary_path))
        report_path = building / "phase3_diagnostics_report.md"
        _write_report(report_path, summary)
        artifacts.append(_artifact(report_path))
        manifest: dict[str, Any] = {
            "schemaVersion": PHASE3_EVIDENCE_INDEX_SCHEMA,
            "programId": lock["programId"],
            "studyType": "ALPHA_STABILITY_REGIME_RESEARCH_DIAGNOSIS_ONLY",
            "contractLock": {
                "path": str(lock_path),
                "sha256": sha256_file(lock_path),
                "lockSha256": lock["lockSha256"],
            },
            "contractLockSha256": lock["lockSha256"],
            "diagnosticPlan": {
                "path": str(plan_source),
                "sha256": sha256_file(plan_source),
                "planSha256": plan["planSha256"],
            },
            "phase2Evidence": locked_evidence,
            "lineage": {
                "dataReleaseId": release.data_release_id,
                "dataReleaseManifestSha256": release.manifest_sha256,
                "datasetVersionId": _mapping(lock["lineage"], "lineage")["datasetVersionId"],
                "featureSnapshotId": locked_feature["featureSnapshotId"],
                "regimeSemanticSha256": spec.semantic_sha256,
                "sourceCodeCommit": revision["commit"],
                "sourceCodeDirty": False,
            },
            "state": "PHASE3_DIAGNOSIS_COMPLETE",
            "completedWorkstreams": ["P3-D00", "P3-D01", "P3-D02", "P3-D03", "P3-D04"],
            "diagnosisOnly": True,
            "formalCandidates": [],
            "formalCandidateCount": 0,
            "confirmationState": "NOT_STARTED",
            "finalHoldoutAccessed": False,
            "selectionUsesFinalHoldout": False,
            "publishingAuthorized": False,
            "summary": summary,
            "artifacts": sorted(artifacts, key=lambda item: str(item["name"])),
        }
        manifest["evidenceSha256"] = sha256_json(manifest)
        manifest_path = building / PHASE3_MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
        )
        try:
            os.replace(building, output)
        except OSError:
            if output.exists():
                return _validate_existing(
                    output, lock=lock, lock_path=lock_path, plan=plan, plan_path=plan_source
                )
            raise
        return output / PHASE3_MANIFEST_NAME
    finally:
        if building.exists():
            shutil.rmtree(building, ignore_errors=True)
