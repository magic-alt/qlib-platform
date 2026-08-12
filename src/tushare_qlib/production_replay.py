from __future__ import annotations

import json
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd

from .model_registry import ModelRegistry
from .pretrade_runner import run_pretrade_actions
from .settings import Paths, Settings


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


def _snapshot_for_date(snapshot_root: Path, signal_date: str) -> Path:
    candidates = [snapshot_root / signal_date, snapshot_root / signal_date.replace("-", "")]
    snapshot = next((path for path in candidates if path.is_dir()), None)
    if snapshot is None:
        raise FileNotFoundError(f"frozen dataset snapshot is missing for {signal_date}")
    manifest_path = snapshot / "dataset_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"frozen dataset manifest is missing for {signal_date}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    smoke = manifest.get("smoke_test", {})
    last_date = smoke.get("last_date") if isinstance(smoke, dict) else None
    if pd.Timestamp(last_date).normalize() != pd.Timestamp(signal_date).normalize():
        raise ValueError(f"frozen dataset snapshot last date does not match {signal_date}")
    return snapshot


def _replay_paths(settings: Settings, output: Path, state: Path) -> Paths:
    source = settings.paths
    return Paths(
        root=source.root,
        raw=source.raw,
        raw_revisions=source.raw_revisions,
        curated=source.curated,
        staging_full=source.staging_full,
        staging_update=source.staging_update,
        staging_repair=source.staging_repair,
        metadata=source.metadata,
        output=output,
        quality=source.quality,
        state=state,
        models=source.models,
    )


def _activate_replay_deployment(
    source_settings: Settings, replay_settings: Settings, deployment_id: str | None
) -> str:
    source = ModelRegistry(source_settings)
    record = source.state.deployment(deployment_id) if deployment_id else source.current()
    identifier = str(record["deployment_id"])
    manifest_path = source.bundle_root(identifier) / "model_manifest.json"
    replay = ModelRegistry(replay_settings)
    replay.register_bundle(manifest_path)
    replay.deploy(identifier)
    return identifier


def run_production_replay(
    settings: Settings,
    *,
    start: str,
    end: str,
    snapshot_root: str | Path,
    deployment_id: str | None = None,
    with_pretrade: bool = False,
) -> Path:
    from .live_inference import run_live_inference

    dates = _open_dates(settings, start, end)
    if not dates:
        raise ValueError("production replay range contains no open trading dates")
    snapshots = Path(snapshot_root).expanduser().resolve()
    snapshot_by_date = {date: _snapshot_for_date(snapshots, date) for date in dates}
    replay_root = settings.paths.output / "replay" / f"{pd.Timestamp(start):%Y%m%d}_{pd.Timestamp(end):%Y%m%d}"
    replay_output = replay_root / "artifacts"
    replay_output.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="replay-state-", dir=replay_root) as temporary_state:
        base_replay = replace(
            settings,
            paths=_replay_paths(settings, replay_output, Path(temporary_state)),
        )
        active_deployment = _activate_replay_deployment(settings, base_replay, deployment_id)
        for signal_date in dates:
            replay_settings = replace(base_replay, qlib_data_uri=snapshot_by_date[signal_date])
            try:
                inference = run_live_inference(
                    replay_settings,
                    as_of=signal_date,
                    deployment_id=active_deployment,
                    require_daily_sync=False,
                    health_now_utc=(pd.Timestamp(signal_date).tz_localize("UTC") + pd.Timedelta(hours=23)).to_pydatetime(),
                )
                manifest = json.loads(inference.manifest_path.read_text(encoding="utf-8"))
                row: dict[str, Any] = {
                    "signalDate": signal_date,
                    "tradeDate": inference.trade_date,
                    "signalId": inference.signal_id,
                    "signalSha256": manifest["signalSha256"],
                    "topkSha256": manifest["artifactPayloads"]["MODEL_TOPK"],
                    "health": inference.health.decision,
                    "reasons": inference.health.reasons,
                }
                inbox = settings.paths.root / "inbox" / "pretrade" / inference.trade_date
                if with_pretrade and inbox.is_dir() and inference.health.passed:
                    action = run_pretrade_actions(
                        replay_settings, trade_date=inference.trade_date, notify=False
                    )
                    row["pretrade"] = {
                        "decision": str(action.decision_path.relative_to(replay_output)),
                        "orders": str(action.orders_path.relative_to(replay_output)),
                        "blocked": str(action.blocked_path.relative_to(replay_output)),
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
        "schemaVersion": "2.0",
        "start": start,
        "end": end,
        "deploymentId": active_deployment,
        "snapshotRoot": str(snapshots),
        "runs": rows,
        "passed": bool(rows) and all(row.get("health") == "PASS" for row in rows),
    }
    target = replay_root / "report.json"
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.", dir=replay_root)
    os.close(fd)
    temporary = Path(name)
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target
