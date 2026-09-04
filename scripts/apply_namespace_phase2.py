from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD = "tushare_qlib"
NEW = "qlib_platform"
SELF = Path(__file__).resolve()
TEXT_SUFFIXES = {
    ".py",
    ".md",
    ".yaml",
    ".yml",
    ".toml",
    ".sh",
    ".ps1",
    ".in",
    ".rules",
    ".txt",
    ".plist",
}
TEXT_NAMES = {"Makefile", "tq"}
SKIP_DIRS = {".git", ".venv", "data", "mlruns", "dist", "build"}
WORKFLOW_DIR = ROOT / ".github" / "workflows"


def eligible(path: Path) -> bool:
    if path.resolve() == SELF:
        return False
    if WORKFLOW_DIR in path.parents:
        return False
    if any(part in SKIP_DIRS for part in path.parts):
        return False
    return path.name in TEXT_NAMES or path.suffix.lower() in TEXT_SUFFIXES


def main() -> None:
    changed = 0
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or not eligible(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if OLD not in text:
            continue
        path.write_text(text.replace(OLD, NEW), encoding="utf-8")
        changed += 1

    legacy = ROOT / "src" / OLD
    removed = 0
    if legacy.is_dir():
        for path in sorted(legacy.rglob("*"), reverse=True):
            if path.is_file() or path.is_symlink():
                path.unlink()
                removed += 1
            elif path.is_dir():
                path.rmdir()
        legacy.rmdir()

    print(f"namespace files changed={changed}; legacy files removed={removed}")


if __name__ == "__main__":
    main()
