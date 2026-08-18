from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from tushare_qlib.p0_baseline import (
    child_audit_receipts,
    cost_stress_test,
    write_orthogonal_synthesis_receipt,
)
from tushare_qlib.store import sha256_file


def test_cost_stress_reports_extra_friction_and_net_excess() -> None:
    report = pd.DataFrame(
        {
            "account": [100_000.0, 100_100.0],
            "return": [0.0, 0.001],
            "bench": [0.0, 0.0],
            "cost": [0.0, 0.0],
        },
        index=pd.to_datetime(["2026-01-05", "2026-01-06"]),
    )
    audit = pd.DataFrame(
        {
            "trade_date": ["2026-01-05", "2026-01-06"],
            "filled_value": [1_000.0, 1_100.0],
        }
    )

    stress = cost_stress_test(report, audit, extra_bps=(0.0, 1.0))

    assert stress["extra_slippage_bps"].tolist() == [0.0, 1.0]
    assert stress.iloc[1]["additional_cost"] == pytest.approx(0.21)
    assert stress.iloc[1]["net_excess_return"] < stress.iloc[0]["net_excess_return"]


def test_orthogonal_synthesis_receipts_are_checksum_backed_and_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "child"
    root.mkdir()
    artifact = root / "audit_reconciliation.parquet"
    artifact.write_bytes(b"audited")
    reconciliation = {
        "passed": True,
        "turnover_match": True,
        "cost_match": True,
        "position_match": True,
        "artifact": artifact.name,
        "artifactSha256": sha256_file(artifact),
        "verifiedAt": "2026-08-18T00:00:00Z",
    }
    manifest = {
        "externalRunId": "child-1",
        "auditReconciliation": reconciliation,
        "execution": {"benchmark": "SH000300", "dealPrice": "open"},
        "signalDiagnostics": {"ic": 0.1, "icir": 0.2, "rankIC": 0.3, "rankICIR": 0.4},
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    receipts = child_audit_receipts([root])
    assert receipts[0]["auditArtifactHash"] == reconciliation["artifactSha256"]
    output = tmp_path / "synthesis_receipt.json"
    write_orthogonal_synthesis_receipt(output, [root])
    assert json.loads(output.read_text(encoding="utf-8"))["auditStatus"] == "PASS"

    reconciliation["passed"] = False
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="rejected failed child audit"):
        child_audit_receipts([root])
