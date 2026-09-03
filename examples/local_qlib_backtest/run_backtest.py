from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


WORKFLOWS = {
    "lightgbm": "workflow_lightgbm.yaml",
    "ridge": "workflow_ridge.yaml",
    "custom_ridge": "workflow_custom_ridge.yaml",
}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Cross-platform local Qlib Alpha158 backtest runner")
    p.add_argument("--model", choices=sorted(WORKFLOWS), default="lightgbm")
    p.add_argument("--workflow")
    p.add_argument("--dataset-ref", default="research-current")
    p.add_argument("--experiment-name", default="")
    p.add_argument("--recorder-uri", default="mlruns/examples_local_backtest")
    p.add_argument("--config", default="configs/pipeline_tushare_dev.yaml")
    return p


def _run(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=capture, check=False)


def _last_json(text: str) -> dict[str, object]:
    for line in reversed([value.strip() for value in text.splitlines() if value.strip()]):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise RuntimeError("dataset-resolve did not return a JSON object")


def _qrun_executable() -> str:
    executable = Path(sys.executable).resolve()
    candidates = [
        executable.parent / ("qrun.exe" if os.name == "nt" else "qrun"),
        Path(shutil.which("qrun") or ""),
    ]
    for candidate in candidates:
        if str(candidate) and candidate.is_file():
            return str(candidate)
    raise RuntimeError("qrun is unavailable; install the repository Qlib/dev extra in the active environment")


def main() -> int:
    args = parser().parse_args()
    example_dir = Path(__file__).resolve().parent
    repo_root = example_dir.parents[1]
    os.chdir(repo_root)

    workflow = Path(args.workflow).expanduser() if args.workflow else example_dir / WORKFLOWS[args.model]
    workflow = workflow.resolve()
    if not workflow.is_file():
        raise FileNotFoundError(f"workflow does not exist: {workflow}")

    config = Path(args.config).expanduser().resolve()
    if not config.is_file():
        raise FileNotFoundError(f"config does not exist: {config}")

    env = os.environ
    env.setdefault("QLIB_REPO", str(repo_root))
    env.setdefault("QLIB_DATA_URI", str((repo_root / "data" / "qlib").resolve()))

    base = [sys.executable, "-m", "tushare_qlib", "--config", str(config)]
    resolved = _run([*base, "dataset-resolve", args.dataset_ref], capture=True)
    if resolved.stdout:
        print(resolved.stdout, end="")
    if resolved.stderr:
        print(resolved.stderr, end="", file=sys.stderr)
    if resolved.returncode:
        return resolved.returncode
    identity = _last_json(resolved.stdout)
    version_id = str(identity.get("versionId") or "")
    data_path = str(identity.get("path") or "")
    if not version_id or not data_path:
        raise RuntimeError("dataset-resolve returned an incomplete DatasetVersion identity")

    verified = _run([*base, "dataset-verify", args.dataset_ref, "--mode", "deep"])
    if verified.returncode:
        return verified.returncode

    immutable_path = Path(data_path).expanduser().resolve()
    if not immutable_path.is_dir():
        raise FileNotFoundError(f"resolved DatasetVersion path is missing: {immutable_path}")
    env["QLIB_DATA_URI"] = str(immutable_path)
    env["MLFLOW_ALLOW_FILE_STORE"] = "true"

    contract = _run([*base, "validate-qrun-contract", "--workflow", str(workflow)])
    if contract.returncode:
        return contract.returncode

    experiment = args.experiment_name or f"local_alpha158_{workflow.stem}"
    print(f"Dataset version: {version_id}")
    print(f"Dataset path: {immutable_path}")
    print(f"Workflow: {workflow}")
    print(f"Experiment: {experiment}")
    return _run([_qrun_executable(), str(workflow), "-e", experiment, "-u", args.recorder_uri]).returncode


if __name__ == "__main__":
    raise SystemExit(main())
