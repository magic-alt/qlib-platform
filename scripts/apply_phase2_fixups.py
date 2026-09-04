from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"expected text not found in {path}: {old!r}")
    target.write_text(text.replace(old, new), encoding="utf-8")


def main() -> None:
    replace(
        "tests/test_walk_forward.py",
        "from qlib_platform import backtest_report",
        "import qlib_platform.backtesting.backtest_report as backtest_report",
    )
    replace(
        "tests/test_walk_forward.py",
        "    from qlib_platform import walk_forward",
        "    import qlib_platform.research.walk_forward as walk_forward",
    )
    replace(
        "tests/test_daily_sync.py",
        "from qlib_platform import daily_sync, qlib_export",
        "import qlib_platform.data.daily_sync as daily_sync\nimport qlib_platform.datasets.qlib_export as qlib_export",
    )
    replace(
        "tests/test_scheduler_assets.py",
        "from qlib_platform import scheduler",
        "import qlib_platform.runtime.scheduler as scheduler",
    )

    namespace_test = ROOT / "tests" / "test_package_namespace.py"
    namespace_test.write_text(
        """from __future__ import annotations\n\nimport importlib\nfrom pathlib import Path\n\nimport pytest\n\n\ndef test_provider_neutral_package_is_canonical():\n    package = importlib.import_module(\"qlib_platform\")\n    assert package.__version__ == \"0.3.0\"\n\n\ndef test_vendor_named_legacy_namespace_is_removed():\n    repository_root = Path(__file__).resolve().parents[1]\n    assert not (repository_root / \"src\" / \"tushare_qlib\").exists()\n    with pytest.raises(ModuleNotFoundError):\n        importlib.import_module(\"tushare_qlib\")\n\n\ndef test_domain_modules_resolve_from_canonical_namespace():\n    module = importlib.import_module(\"qlib_platform.backtesting.strategy_contract\")\n    assert module.__file__ is not None\n    assert \"qlib_platform/backtesting\" in module.__file__.replace(\"\\\\\", \"/\")\n""",
        encoding="utf-8",
    )

    replace(
        "docs/daily_sync.md",
        "- `data/bronze/tushare/current/` 是完整且唯一的本地 raw working view；变更分区会在此原子替换，不再生成平行的 `revisions/` 数据集。可复现性由发布时冻结的 `data/bronze/versions/` 和 immutable DataRelease 保证。",
        "- `data/bronze/market/current/` 是完整且唯一的 provider-neutral raw working view；变更分区会在此原子替换，不再生成平行的 `revisions/` 数据集。具体数据供应商属于 manifest/config provenance，不再进入 canonical storage identity。可复现性由发布时冻结的 immutable DataRelease/DatasetVersion 保证。\n- 历史 `data/bronze/tushare/` 不会被原地改名或删除；`migrate-qlib-layout --apply` 会在完整文件数、大小和字节校验后物化到 `data/bronze/market/`，保留旧目录与已有 manifest/DatasetVersion 身份。若同时发现更老的 `data/raw/` 与 `data/bronze/tushare/`，迁移 fail closed，要求先明确历史来源。",
    )
    replace(
        "docs/daily_sync.md",
        "- 空的 legacy `data/bronze/tushare/current/extended/hsgt_moneyflow/` 目录会在日更时清理；正确的 TuShare endpoint/目录名称是 `moneyflow_hsgt`。",
        "- 空的 `data/bronze/market/current/extended/hsgt_moneyflow/` 目录会在日更时清理；当前 TuShare adapter 的正确 endpoint/目录名称是 `moneyflow_hsgt`。",
    )

    replace(
        "docs/qlib_data_platform.md",
        "├── bronze/tushare/current/       # replaceable materialized view used by normalization",
        "├── bronze/market/current/        # provider-neutral replaceable market-data working view",
    )
    replace(
        "docs/qlib_data_platform.md",
        "`bronze/tushare/current` is the single complete local raw-data view; daily updates atomically replace\nchanged partitions there and do not create a parallel `revisions` dataset. `current` directories are\nworking views, not auditable versions.",
        "`bronze/market/current` is the single complete local raw-data view; daily updates atomically replace\nchanged partitions there and do not create a parallel `revisions` dataset. The storage path describes the\nsemantic layer rather than the API vendor; provider provenance remains in manifests/configuration. `current`\ndirectories are working views, not auditable versions.",
    )
    replace(
        "docs/qlib_data_platform.md",
        "The command journals every step under `data/.migration/` and preserves every legacy source directory in\nits original location.",
        "The command journals every step under `data/.migration/` and preserves every legacy source directory in\nits original location. In particular, the pre-0.4 `data/bronze/tushare/` tree is materialized byte-for-byte\ninto `data/bronze/market/`; the older `data/raw/` layout is also supported. If both are present, migration\nfails closed instead of merging potentially different market histories. Existing manifests and DatasetVersion\nidentities are not rewritten merely to normalize a directory name.",
    )

    print("phase-2 semantic/test fixups applied")


if __name__ == "__main__":
    main()
