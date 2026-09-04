from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

from qlib_platform.backtesting.execution_audit import reconciliation_manifest, reconcile_execution, require_reconciliation
from qlib_platform.backtesting.signal_diagnostics import build_signal_diagnostics
from qlib_platform.data.store import sha256_file


def cost_stress_test(
    portfolio_report: pd.DataFrame,
    audit: pd.DataFrame,
    *,
    extra_bps: Iterable[float] = (0.0, 1.0, 2.0, 3.0, 5.0, 10.0),
) -> pd.DataFrame:
    """Apply additional execution friction to the actual filled order values.

    This is an accounting stress test, not a fill-model replacement: it holds
    observed fills fixed and answers how much extra per-notional slippage the
    recorded strategy can absorb.
    """

    report = portfolio_report.copy()
    report.index = pd.to_datetime(report.index, errors="raise").normalize()
    report = report.loc[~report.index.duplicated(keep="last")].sort_index()
    if "account" not in report:
        raise ValueError("portfolio report missing account for cost stress")
    audit_frame = audit.copy()
    audit_frame["trade_date"] = pd.to_datetime(audit_frame["trade_date"], errors="raise").dt.normalize()
    order_value = pd.to_numeric(audit_frame.get("filled_value", 0.0), errors="coerce").fillna(0.0).abs()
    daily_value = order_value.groupby(audit_frame["trade_date"]).sum().reindex(report.index, fill_value=0.0)
    returns = pd.to_numeric(report.get("return", report["account"].pct_change()), errors="coerce").fillna(0.0)
    costs = pd.to_numeric(report.get("cost", 0.0), errors="coerce").fillna(0.0)
    bench = pd.to_numeric(report.get("bench", 0.0), errors="coerce").fillna(0.0)
    initial_account = float(report["account"].iloc[0] / max(1.0 + returns.iloc[0] - costs.iloc[0], 1e-12))
    rows: list[dict[str, float]] = []
    for bps in extra_bps:
        if bps < 0:
            raise ValueError("extra slippage bps must be non-negative")
        additional = daily_value * float(bps) / 10_000.0
        prior_account = report["account"].shift(1).fillna(initial_account).clip(lower=1e-12)
        stressed_daily = returns - costs - additional / prior_account
        terminal = initial_account * float((1.0 + stressed_daily).prod())
        benchmark_terminal = float((1.0 + bench).prod())
        rows.append(
            {
                "extra_slippage_bps": float(bps),
                "additional_cost": float(additional.sum()),
                "net_return": terminal / initial_account - 1.0,
                "benchmark_return": benchmark_terminal - 1.0,
                "net_excess_return": terminal / initial_account - benchmark_terminal,
            }
        )
    return pd.DataFrame(rows)


def write_p0_artifacts(
    run_dir: str | Path,
    *,
    strict_reconciliation: bool = True,
) -> Mapping[str, Any]:
    """Persist the P0 baseline evidence alongside an existing research run."""

    root = Path(run_dir).expanduser().resolve()
    report = pd.read_parquet(root / "portfolio_report.parquet")
    audit = pd.read_parquet(root / "strategy_audit.parquet")
    reconciliation, result = reconcile_execution(audit, report)
    reconciliation_path = root / "audit_reconciliation.parquet"
    reconciliation.to_parquet(reconciliation_path, index=False)
    stress = cost_stress_test(report, audit)
    stress_path = root / "cost_stress.parquet"
    stress.to_parquet(stress_path, index=False)
    verified_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    payload: dict[str, Any] = {
        "auditReconciliation": {
            **reconciliation_manifest(result),
            "artifact": reconciliation_path.name,
            "artifactSha256": sha256_file(reconciliation_path),
            "verifiedAt": verified_at,
        },
        "costStress": {
            "artifact": stress_path.name,
            "artifactSha256": sha256_file(stress_path),
            "extraSlippageBps": stress["extra_slippage_bps"].tolist(),
        },
    }
    prediction_path = root / "oos_predictions.parquet"
    label_path = root / "oos_labels.parquet"
    if prediction_path.is_file() and label_path.is_file():
        daily, summary = build_signal_diagnostics(
            pd.read_parquet(prediction_path), pd.read_parquet(label_path)
        )
        diagnostics_path = root / "signal_diagnostics.parquet"
        daily.to_parquet(diagnostics_path, index=False)
        summary_path = root / "signal_diagnostics_summary.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        payload["signalDiagnostics"] = {"artifact": diagnostics_path.name, **summary}
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    manifest.update(payload)
    artifacts = manifest.setdefault("artifacts", [])
    if not isinstance(artifacts, list):
        raise ValueError("manifest artifacts must be a list")
    artifact_paths = [reconciliation_path, stress_path]
    if "signalDiagnostics" in payload:
        artifact_paths.extend([diagnostics_path, summary_path])
    for path in artifact_paths:
        entry = {"name": path.name, "localPath": str(path), "sha256": sha256_file(path)}
        for index, item in enumerate(artifacts):
            if isinstance(item, Mapping) and str(item.get("name")) == path.name:
                artifacts[index] = entry
                break
        else:
            artifacts.append(entry)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if strict_reconciliation:
        require_reconciliation(result)
    return payload


def child_audit_receipts(run_dirs: Iterable[str | Path]) -> list[dict[str, Any]]:
    """Load checksum-backed child audits or fail the orthogonal synthesis closed."""

    receipts: list[dict[str, Any]] = []
    shared_metadata: dict[str, Any] | None = None
    for raw in run_dirs:
        root = Path(raw).expanduser().resolve()
        manifest_path = root / "manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(f"child manifest is missing: {manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        reconciliation = manifest.get("auditReconciliation")
        flags = ("passed", "turnover_match", "cost_match", "position_match")
        if not isinstance(reconciliation, Mapping) or not all(
            reconciliation.get(key) is True for key in flags
        ):
            raise ValueError(f"orthogonal synthesis rejected failed child audit: {root}")
        execution = manifest.get("execution")
        diagnostics = manifest.get("signalDiagnostics")
        metric_names = ("ic", "icir", "rankIC", "rankICIR")
        if not isinstance(execution, Mapping) or not isinstance(diagnostics, Mapping):
            raise ValueError(f"child signal or execution metadata is missing: {root}")
        if not all(name in diagnostics for name in metric_names):
            raise ValueError(f"child signal diagnostics are incomplete: {root}")
        metadata = {
            "benchmark": execution.get("benchmark"),
            "dealPrice": execution.get("dealPrice"),
            "signalDiagnostics": {name: diagnostics[name] for name in metric_names},
        }
        if metadata["benchmark"] is None or metadata["dealPrice"] is None:
            raise ValueError(f"child benchmark or deal-price metadata is missing: {root}")
        if shared_metadata is None:
            shared_metadata = metadata
        elif shared_metadata != metadata:
            raise ValueError(f"orthogonal synthesis rejected inconsistent child metadata: {root}")
        artifact_name = str(reconciliation.get("artifact") or "audit_reconciliation.parquet")
        artifact_path = root / artifact_name
        expected_hash = reconciliation.get("artifactSha256")
        verified_at = reconciliation.get("verifiedAt")
        if not artifact_path.is_relative_to(root) or not artifact_path.is_file():
            raise FileNotFoundError(
                f"child audit artifact is missing or escapes run directory: {artifact_path}"
            )
        if not isinstance(expected_hash, str) or sha256_file(artifact_path) != expected_hash:
            raise ValueError(f"child audit artifact checksum mismatch: {artifact_path}")
        if not isinstance(verified_at, str) or not verified_at:
            raise ValueError(f"child audit verification timestamp is missing: {root}")
        receipts.append(
            {
                "runId": str(manifest.get("externalRunId") or root.name),
                "auditArtifact": artifact_name,
                "auditArtifactHash": expected_hash,
                "turnoverReconciled": True,
                "costReconciled": True,
                "positionIdentityReconciled": True,
                "benchmark": metadata["benchmark"],
                "dealPrice": metadata["dealPrice"],
                "signalDiagnostics": metadata["signalDiagnostics"],
                "verifiedAt": verified_at,
            }
        )
    if not receipts:
        raise ValueError("orthogonal synthesis requires at least one child run")
    return receipts


def write_orthogonal_synthesis_receipt(output_path: str | Path, run_dirs: Iterable[str | Path]) -> Path:
    """Persist a synthesis receipt only after every child audit has verified."""

    receipts = child_audit_receipts(run_dirs)
    destination = Path(output_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schemaVersion": "p0_orthogonal_audit_receipt_v1",
        "auditStatus": "PASS",
        "sharedMetadata": {
            "benchmark": receipts[0]["benchmark"],
            "dealPrice": receipts[0]["dealPrice"],
            "signalDiagnostics": receipts[0]["signalDiagnostics"],
        },
        "childAudits": receipts,
    }
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return destination
