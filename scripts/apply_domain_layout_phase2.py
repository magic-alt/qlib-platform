from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "qlib_platform"
WORKFLOW_DIR = ROOT / ".github" / "workflows"
SKIP_DIRS = {".git", ".venv", "data", "mlruns", "dist", "build"}
TEXT_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".toml", ".sh", ".ps1", ".in", ".rules", ".txt", ".plist"}
TEXT_NAMES = {"Makefile", "tq"}

# Old root module -> canonical module after the phase-2 domain split.
MODULE_MAP: dict[str, str] = {
    "_extract_legacy": "data._legacy_ingestion",
    "client": "data.sources",
    "extract": "data.ingestion",
    "mysql_source": "data.sources.mysql",
    "fundamentals": "data.fundamentals",
    "industry": "data.industry",
    "content_store": "data.content_store",
    "corporate_actions": "data.corporate_actions",
    "custom_handler": "data.custom_handler",
    "daily_sync": "data.daily_sync",
    "extended_data": "data.extended_data",
    "extended_parallel": "data.extended_parallel",
    "kline_export": "data.kline_export",
    "normalize": "data.normalize",
    "processor_state": "data.processor_state",
    "processors": "data.processors",
    "quality": "data.quality",
    "store": "data.store",
    "symbols": "data.symbols",
    "universe": "data.universe",
    "data_release": "datasets.data_release",
    "data_source_resolver": "datasets.data_source_resolver",
    "dataset_manifest": "datasets.dataset_manifest",
    "dataset_registry": "datasets.dataset_registry",
    "dataset_resolver": "datasets.dataset_resolver",
    "lakehouse": "datasets.lakehouse",
    "layout_migration": "datasets.layout_migration",
    "migration_acceptance": "datasets.migration_acceptance",
    "qlib_export": "datasets.qlib_export",
    "verification": "datasets.verification",
    "artifact_resolver": "artifacts.artifact_resolver",
    "institutional_artifacts": "artifacts.institutional_artifacts",
    "live_artifacts": "artifacts.live_artifacts",
    "prediction_snapshot": "artifacts.prediction_snapshot",
    "research_bundle_export": "artifacts.research_bundle_export",
    "backtest_audit": "backtesting.backtest_audit",
    "backtest_report": "backtesting.backtest_report",
    "execution_audit": "backtesting.execution_audit",
    "exposure_overlay": "backtesting.exposure_overlay",
    "portfolio": "backtesting.portfolio",
    "prediction_backtest": "backtesting.prediction_backtest",
    "qlib_strategies": "backtesting.qlib_strategies",
    "signal_diagnostics": "backtesting.signal_diagnostics",
    "strategies": "backtesting.strategies",
    "strategy_audit": "backtesting.strategy_audit",
    "strategy_contract": "backtesting.strategy_contract",
    "strategy_factory": "backtesting.strategy_factory",
    "strategy_targets": "backtesting.strategy_targets",
    "topk_dropout": "backtesting.topk_dropout",
    "trade_plan": "backtesting.trade_plan",
    "workflow_records": "backtesting.workflow_records",
    "daily_signal_runner": "runtime.daily_signal_runner",
    "failure_codes": "runtime.failure_codes",
    "file_lock": "runtime.file_lock",
    "health": "runtime.health",
    "live_inference": "runtime.live_inference",
    "live_parity": "runtime.live_parity",
    "monitoring": "runtime.monitoring",
    "runtime_resources": "runtime.runtime_resources",
    "runtime_safety": "runtime.runtime_safety",
    "scheduler": "runtime.scheduler",
    "signal_health": "runtime.signal_health",
    "standalone_status": "runtime.standalone_status",
    "delivery_ledger": "ops.delivery_ledger",
    "lean_bridge": "ops.lean_bridge",
    "lean_integration": "ops.lean_integration",
    "ops_cli": "ops.ops_cli",
    "ops_state": "ops.ops_state",
    "platform_release": "ops.platform_release",
    "model_bundle": "models.model_bundle",
    "model_registry": "models.model_registry",
    "model_runtime": "models.model_runtime",
    "production_refit": "models.production_refit",
    "feature_store": "research.feature_store",
    "full_walk_forward_acceptance": "research.full_walk_forward_acceptance",
    "p0_baseline": "research.p0_baseline",
    "research_cli_ux": "research.research_cli_ux",
    "research_experiment": "research.research_experiment",
    "research_gate": "research.research_gate",
    "research_quickstart": "research.research_quickstart",
    "research_summary": "research.research_summary",
    "research_timing": "research.research_timing",
    "train_select": "research.train_select",
    "walk_forward": "research.walk_forward",
    "walk_forward_acceptance": "research.walk_forward_acceptance",
}

# These phase-1 root modules are shims whose canonical implementation already exists.
DELETE_SHIMS = {"client.py", "extract.py", "mysql_source.py", "fundamentals.py", "industry.py"}

# The artifact contract keeps qlib_platform.artifacts as its public import surface,
# but the implementation moves from artifacts.py into the package.
SPECIAL_ARTIFACT_SOURCE = PACKAGE / "artifacts.py"


def _module_for_path(path: Path) -> str:
    rel = path.relative_to(PACKAGE).with_suffix("")
    return ".".join(("qlib_platform", *rel.parts))


def _resolve_relative(current_module: str, level: int, module: str | None) -> str:
    package_parts = current_module.split(".")[:-1]
    keep = len(package_parts) - (level - 1)
    if keep < 1:
        raise RuntimeError(f"relative import escapes package: {current_module} level={level}")
    parts = package_parts[:keep]
    if module:
        parts.extend(module.split("."))
    return ".".join(parts)


def _map_absolute(module: str) -> str:
    prefix = "qlib_platform."
    if not module.startswith(prefix):
        return module
    tail = module[len(prefix):]
    root_name, dot, rest = tail.partition(".")
    mapped = MODULE_MAP.get(root_name)
    if mapped is None:
        return module
    result = f"qlib_platform.{mapped}"
    return f"{result}.{rest}" if dot else result


def _normalize_python_imports(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    lines = text.splitlines(keepends=True)
    current = _module_for_path(path)
    changed = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level <= 0:
            continue
        absolute = _map_absolute(_resolve_relative(current, node.level, node.module))
        line_index = node.lineno - 1
        line = lines[line_index]
        if node.module is None:
            # Keep `from package import name` only when none of the names is a moved root module.
            moved = [alias.name for alias in node.names if alias.name in MODULE_MAP]
            if moved:
                raise RuntimeError(
                    f"manual rewrite required for moved from-import in {path}:{node.lineno}: {moved}"
                )
            pattern = r"(\bfrom\s+)\.+(\s+import\b)"
        else:
            pattern = rf"(\bfrom\s+)\.{{{node.level}}}{re.escape(node.module)}(\s+import\b)"
        rewritten, count = re.subn(pattern, rf"\1{absolute}\2", line, count=1)
        if count != 1:
            raise RuntimeError(f"could not normalize import in {path}:{node.lineno}: {line.rstrip()}")
        lines[line_index] = rewritten
        changed = True
    if changed:
        path.write_text("".join(lines), encoding="utf-8")


def _eligible_text(path: Path) -> bool:
    if WORKFLOW_DIR in path.parents:
        return False
    if any(part in SKIP_DIRS for part in path.parts):
        return False
    return path.name in TEXT_NAMES or path.suffix.lower() in TEXT_SUFFIXES


def _rewrite_module_references() -> int:
    replacements = [
        (f"qlib_platform.{old}", f"qlib_platform.{new}")
        for old, new in MODULE_MAP.items()
    ]
    replacements.sort(key=lambda item: len(item[0]), reverse=True)
    changed = 0
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or not _eligible_text(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        updated = text
        for old, new in replacements:
            updated = updated.replace(old, new)
        if updated != text:
            path.write_text(updated, encoding="utf-8")
            changed += 1
    return changed


def _move_root_module(old: str, new: str) -> None:
    if old in {"client", "extract", "mysql_source", "fundamentals", "industry"}:
        return
    source = PACKAGE / f"{old}.py"
    if not source.is_file():
        raise RuntimeError(f"expected root module is missing: {source}")
    target = PACKAGE / (new.replace(".", "/") + ".py")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise RuntimeError(f"target already exists: {target}")
    source.replace(target)


def _write_package_files() -> None:
    for name in ("artifacts", "backtesting", "datasets", "ops", "runtime"):
        package = PACKAGE / name
        package.mkdir(parents=True, exist_ok=True)
        init = package / "__init__.py"
        if not init.exists():
            init.write_text(f'"""{name.capitalize()} domain package."""\n', encoding="utf-8")

    artifacts_init = PACKAGE / "artifacts" / "__init__.py"
    artifacts_init.write_text(
        '"""Research artifact contracts and publication helpers."""\n\n'
        "from qlib_platform.artifacts.contracts import (\n"
        "    ARTIFACT_SCHEMA_VERSION,\n"
        "    ArtifactContractError,\n"
        "    ArtifactType,\n"
        "    PromotionStatus,\n"
        "    load_artifact_manifest,\n"
        "    stamp_artifact,\n"
        "    validate_artifact,\n"
        "    validate_manifest_portfolio_policy,\n"
        ")\n\n"
        "__all__ = [\n"
        '    "ARTIFACT_SCHEMA_VERSION",\n'
        '    "ArtifactContractError",\n'
        '    "ArtifactType",\n'
        '    "PromotionStatus",\n'
        '    "load_artifact_manifest",\n'
        '    "stamp_artifact",\n'
        '    "validate_artifact",\n'
        '    "validate_manifest_portfolio_policy",\n'
        "]\n",
        encoding="utf-8",
    )


def main() -> None:
    # Normalize relative imports while every file is still at its original path.
    for path in sorted(PACKAGE.rglob("*.py")):
        _normalize_python_imports(path)

    rewritten = _rewrite_module_references()

    # Move the artifact contract first because a file and directory cannot share a name.
    if not SPECIAL_ARTIFACT_SOURCE.is_file():
        raise RuntimeError("root artifact contract is missing")
    artifact_tmp = PACKAGE / "_artifact_contracts_phase2.py"
    SPECIAL_ARTIFACT_SOURCE.replace(artifact_tmp)
    (PACKAGE / "artifacts").mkdir(parents=True, exist_ok=True)
    artifact_tmp.replace(PACKAGE / "artifacts" / "contracts.py")

    for old, new in MODULE_MAP.items():
        _move_root_module(old, new)

    for name in DELETE_SHIMS:
        path = PACKAGE / name
        if path.exists():
            path.unlink()

    _write_package_files()

    remaining = sorted(
        path.name
        for path in PACKAGE.glob("*.py")
        if path.name not in {
            "__init__.py",
            "__main__.py",
            "bootstrap.py",
            "canonical_config.py",
            "cli.py",
            "docs_check.py",
            "lineage.py",
            "project_audit.py",
            "settings.py",
            "workflow_contract.py",
        }
    )
    if remaining:
        raise RuntimeError(f"unexpected flat implementation modules remain: {remaining}")

    print(f"domain layout applied; text references rewritten in {rewritten} files")


if __name__ == "__main__":
    main()
