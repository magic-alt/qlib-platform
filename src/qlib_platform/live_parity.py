from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def _load_score(path: str | Path, signal_date: str) -> pd.DataFrame:
    source = Path(path)
    frame = pd.read_parquet(source) if source.suffix.lower() == ".parquet" else pd.read_csv(source)
    if isinstance(frame.index, pd.MultiIndex) or frame.index.name:
        frame = frame.reset_index()
    if "datetime" in frame.columns:
        dates = pd.to_datetime(frame["datetime"], errors="raise").dt.normalize()
    elif "signal_date" in frame.columns:
        dates = pd.to_datetime(frame["signal_date"], errors="raise").dt.normalize()
    else:
        raise ValueError(f"score artifact has no datetime/signal_date column: {source}")
    selected = frame.loc[dates == pd.Timestamp(signal_date).normalize(), ["instrument", "score"]].copy()
    if selected.empty:
        raise ValueError(f"score artifact has no rows for {signal_date}: {source}")
    selected["instrument"] = selected["instrument"].astype(str).str.upper().str.strip()
    selected["score"] = pd.to_numeric(selected["score"], errors="raise")
    if selected["instrument"].duplicated().any() or not np.isfinite(selected["score"]).all():
        raise ValueError(f"score artifact is not a unique finite cross-section: {source}")
    selected = selected.sort_values(["score", "instrument"], ascending=[False, True]).reset_index(drop=True)
    selected["rank"] = np.arange(1, len(selected) + 1, dtype=int)
    return selected


def score_sha256(frame: pd.DataFrame) -> str:
    rows = [
        [str(row.instrument), format(float(row.score), ".17g"), int(row.rank)]
        for row in frame.itertuples(index=False)
    ]
    encoded = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def topk_sha256(frame: pd.DataFrame, topk: int) -> str:
    encoded = json.dumps(
        frame.head(topk)["instrument"].astype(str).tolist(), ensure_ascii=False, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def compare_research_live_scores(
    research_path: str | Path,
    live_path: str | Path,
    *,
    signal_date: str,
    topk: int,
    output_path: str | Path | None = None,
    rtol: float = 1e-6,
    atol: float = 1e-8,
) -> dict[str, Any]:
    if topk <= 0:
        raise ValueError("topk must be positive")
    research = _load_score(research_path, signal_date)
    live = _load_score(live_path, signal_date)
    research_indexed = research.set_index("instrument")
    live_indexed = live.set_index("instrument")
    same_universe = research_indexed.index.equals(live_indexed.index)
    # Compare after an instrument sort; rank/hash checks below preserve ranking semantics.
    common = research_indexed.index.intersection(live_indexed.index)
    differences = (
        np.abs(
            research_indexed.loc[common, "score"].sort_index().to_numpy()
            - live_indexed.loc[common, "score"].sort_index().to_numpy()
        )
        if len(common)
        else np.asarray([], dtype=float)
    )
    scores_close = bool(
        same_universe
        and np.allclose(
            research_indexed["score"].sort_index().to_numpy(),
            live_indexed["score"].sort_index().to_numpy(),
            rtol=rtol,
            atol=atol,
        )
    )
    research_topk = topk_sha256(research, topk)
    live_topk = topk_sha256(live, topk)
    report: dict[str, Any] = {
        "schemaVersion": "1.0",
        "signalDate": pd.Timestamp(signal_date).strftime("%Y-%m-%d"),
        "topk": topk,
        "researchRows": len(research),
        "liveRows": len(live),
        "sameUniverse": same_universe,
        "scoresClose": scores_close,
        "maxAbsScoreDifference": float(differences.max()) if len(differences) else None,
        "researchScoreSha256": score_sha256(research),
        "liveScoreSha256": score_sha256(live),
        "researchTopkSha256": research_topk,
        "liveTopkSha256": live_topk,
        "passed": scores_close and research_topk == live_topk,
    }
    if output_path is not None:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
    return report
