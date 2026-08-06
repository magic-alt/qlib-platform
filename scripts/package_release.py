from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

EXCLUDED_PARTS = {".git", ".venv", "venv", "mlruns", "data", "__pycache__", ".pytest_cache", ".mypy_cache"}
EXCLUDED_NAMES = {".env", ".DS_Store", "MANIFEST.sha256.json"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        while chunk := fp.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def release_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.name not in EXCLUDED_NAMES
        and not any(part in EXCLUDED_PARTS for part in path.relative_to(root).parts)
        and not path.suffix.endswith(".pyc")
    )


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = {str(path.relative_to(root)): sha256(path) for path in release_files(root)}
    manifest_path = root / "MANIFEST.sha256.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    output = root.parent / f"{root.name}.zip"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in release_files(root) + [manifest_path]:
            archive.write(path, arcname=f"{root.name}/{path.relative_to(root)}")
    print(output)


if __name__ == "__main__":
    main()
