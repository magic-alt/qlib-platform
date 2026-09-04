from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "qlib_platform"


def _imports(path: Path) -> list[dict[str, object]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []
    result: list[dict[str, object]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level:
            result.append(
                {
                    "level": node.level,
                    "module": node.module,
                    "names": [alias.name for alias in node.names],
                }
            )
    return result


def main() -> None:
    root_modules = sorted(path.name for path in PACKAGE.glob("*.py"))
    relative_imports = {
        path.name: _imports(path)
        for path in sorted(PACKAGE.glob("*.py"))
        if path.name not in {"__init__.py", "__main__.py"}
    }
    vendor_refs: list[str] = []
    storage_refs: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in {".git", ".venv", "data", "mlruns"} for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = path.relative_to(ROOT).as_posix()
        for lineno, line in enumerate(text.splitlines(), start=1):
            if "tushare_qlib" in line:
                vendor_refs.append(f"{rel}:{lineno}:{line.strip()}")
            if "bronze/tushare" in line or '"tushare" / "current"' in line or '"bronze" / "tushare"' in line:
                storage_refs.append(f"{rel}:{lineno}:{line.strip()}")
    print("ROOT_MODULES=" + json.dumps(root_modules, ensure_ascii=False))
    print("VENDOR_REFS=" + json.dumps(vendor_refs, ensure_ascii=False))
    print("STORAGE_REFS=" + json.dumps(storage_refs, ensure_ascii=False))
    print("RELATIVE_IMPORTS=" + json.dumps(relative_imports, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
