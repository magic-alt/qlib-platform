from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected follow-up block not found in {path}: {old!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "tests/test_bootstrap.py",
    '        "data_path": str(data_path),\n        "coverage": {},\n',
    '        "data_path": str(data_path),\n        "data_release_id": release_id,\n        "coverage": {},\n',
)
replace_once(
    "tests/test_research_quickstart.py",
    '    plan = {"datasetRef": settings.qlib_dataset_ref, "jobs": []}\n',
    '    plan = {"datasetRef": settings.qlib_dataset_ref, "mode": args.mode, "jobs": []}\n',
)
