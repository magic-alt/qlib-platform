import hashlib
import json
from pathlib import Path

import pandas as pd

from qlib_platform.artifacts import ArtifactType
from qlib_platform.lean_bridge import export_lean_targets


def test_lean_export_has_valid_checksum(tmp_path: Path, governed_artifact):
    targets = governed_artifact(
        pd.DataFrame(
            {"instrument": ["SH600000", "SZ000001"], "target_weight": [0.4, 0.3], "score": [1.0, 0.5]}
        ),
        ArtifactType.TARGET_PORTFOLIO,
    )
    json_path, csv_path = export_lean_targets(
        targets,
        tmp_path,
        signal_date="2026-08-06",
        trade_date="2026-08-07",
        model_id="model-1",
        dataset_id="dataset-1",
    )
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    canonical = json.dumps(
        payload["targets"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    assert payload["targets_sha256"] == hashlib.sha256(canonical).hexdigest()
    assert csv_path.exists()
    assert payload["targets"][0]["market"] in {"XSHG", "XSHE"}
