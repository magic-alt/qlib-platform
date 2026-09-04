from __future__ import annotations

import runpy
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "qlib_platform"

# These modules were moved into the data domain by the phase-2 codemod. The old
# repository-wide `data` ignore rule caused Git to record the source deletion while
# silently omitting the new target files. Restore them from the phase-2 base and run
# the same import normalizer used by the original codemod.
DATA_MOVES = {
    "_extract_legacy": "data._legacy_ingestion",
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
}


def _replace_if_present(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old in text:
        path.write_text(text.replace(old, new), encoding="utf-8")


def _restore_data_domain() -> None:
    helpers = runpy.run_path(str(ROOT / "scripts" / "apply_domain_layout_phase2.py"))
    normalize = helpers["_normalize_python_imports"]

    for old, new in DATA_MOVES.items():
        target = PACKAGE / (new.replace(".", "/") + ".py")
        if target.is_file():
            continue
        source = PACKAGE / f"{old}.py"
        if source.exists():
            raise RuntimeError(f"unexpected root implementation still exists: {source}")
        result = subprocess.run(
            ["git", "show", f"origin/main:src/qlib_platform/{old}.py"],
            check=True,
            capture_output=True,
            text=True,
        )
        source.write_text(result.stdout, encoding="utf-8")
        normalize(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        source.replace(target)

        # The legacy base previously imported the compatibility client shim. Keep the
        # provider-neutral protocol import public, but bind the concrete TuShare
        # adapter explicitly now that the shim itself is gone.
        if old == "_extract_legacy":
            _replace_if_present(
                target,
                "from qlib_platform.data.sources import DataSourceClient, RetryPolicy, TushareClient",
                "from qlib_platform.data.sources import DataSourceClient, RetryPolicy\n"
                "from qlib_platform.data.sources.tushare import TushareClient",
            )


def _make_package_initializers_lightweight() -> None:
    (PACKAGE / "data" / "__init__.py").write_text(
        '"""Provider-neutral ingestion, market-data contracts, and storage primitives.\n\n'
        "Import concrete services from their domain modules. Keeping package import\n"
        "lightweight prevents unrelated storage/symbol imports from initializing the\n"
        "ingestion provider stack.\n"
        '"""\n',
        encoding="utf-8",
    )
    (PACKAGE / "research" / "__init__.py").write_text(
        '"""Governed research workflows and diagnostics.\n\n'
        "The package initializer intentionally has no eager re-exports: research\n"
        "modules depend on lineage, while lineage is also used by research contracts.\n"
        "Callers should import the concrete research submodule they use.\n"
        '"""\n',
        encoding="utf-8",
    )


def _repair_lineage_paths() -> None:
    feature_store = PACKAGE / "research" / "feature_store.py"
    _replace_if_present(
        feature_store,
        '    package_root = Path(__file__).resolve().parent\n',
        '    package_root = Path(__file__).resolve().parents[1]\n',
    )
    _replace_if_present(
        feature_store,
        '        package_root / "custom_handler.py",\n'
        '        package_root / "data" / "fundamentals.py",\n',
        '        package_root / "data" / "custom_handler.py",\n'
        '        package_root / "data" / "fundamentals.py",\n',
    )

    walk_forward = PACKAGE / "research" / "walk_forward.py"
    _replace_if_present(
        walk_forward,
        '    project_root = Path(__file__).resolve().parents[2]\n',
        '    project_root = Path(__file__).resolve().parents[3]\n',
    )
    _replace_if_present(
        walk_forward,
        '    package_root = Path(__file__).resolve().parent\n'
        '    source_files = [\n'
        '        Path(__file__),\n'
        '        package_root / "custom_handler.py",\n'
        '        package_root / "processors.py",\n'
        '        package_root / "research_timing.py",\n'
        '        package_root / "model_runtime.py",\n'
        '        package_root / "processor_state.py",\n'
        '        package_root / "train_select.py",\n'
        '        package_root / "prediction_snapshot.py",\n'
        '        package_root / "walk_forward_acceptance.py",\n'
        '    ]\n',
        '    package_root = Path(__file__).resolve().parents[1]\n'
        '    source_files = [\n'
        '        Path(__file__),\n'
        '        package_root / "data" / "custom_handler.py",\n'
        '        package_root / "data" / "processors.py",\n'
        '        package_root / "research" / "research_timing.py",\n'
        '        package_root / "models" / "model_runtime.py",\n'
        '        package_root / "data" / "processor_state.py",\n'
        '        package_root / "research" / "train_select.py",\n'
        '        package_root / "artifacts" / "prediction_snapshot.py",\n'
        '        package_root / "research" / "walk_forward_acceptance.py",\n'
        '    ]\n',
    )
    _replace_if_present(
        walk_forward,
        '            Path(__file__).resolve().parent / "prediction_backtest.py"\n',
        '            Path(__file__).resolve().parents[1] / "backtesting" / "prediction_backtest.py"\n',
    )


def main() -> None:
    _restore_data_domain()
    _make_package_initializers_lightweight()
    _repair_lineage_paths()
    print("phase-2 data-domain recovery and lineage fixups applied")


if __name__ == "__main__":
    main()
