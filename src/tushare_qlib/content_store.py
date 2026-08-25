from __future__ import annotations

import errno
import os
import shutil
import tempfile
from pathlib import Path

from .store import sha256_file


class ContentAddressedStore:
    """Immutable file objects with hard-link materialization and safe copy fallback."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()

    def object_path(self, digest: str) -> Path:
        if len(digest) != 64 or any(value not in "0123456789abcdef" for value in digest):
            raise ValueError("content digest must be a lowercase SHA-256")
        return self.root / digest[:2] / digest

    def store(self, source: str | Path, *, digest: str | None = None) -> tuple[Path, str]:
        original = Path(source).expanduser().resolve()
        if not original.is_file() or original.is_symlink():
            raise ValueError(f"content source must be a regular file: {original}")
        verified_digest = digest or sha256_file(original)
        target = self.object_path(verified_digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if not target.is_file() or target.is_symlink() or sha256_file(target) != verified_digest:
                raise ValueError(f"content-addressed object is corrupt: {target}")
            return target, verified_digest

        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{verified_digest}.", dir=target.parent)
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            shutil.copy2(original, temporary)
            if sha256_file(temporary) != verified_digest:
                raise ValueError(f"content changed while being stored: {original}")
            try:
                os.replace(temporary, target)
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EEXIST} or not target.is_file():
                    raise
                temporary.unlink(missing_ok=True)
            if sha256_file(target) != verified_digest:
                raise ValueError(f"content-addressed object is corrupt: {target}")
            return target, verified_digest
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def materialize(source: str | Path, target: str | Path) -> None:
        original = Path(source)
        destination = Path(target)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(original, destination)
        except OSError:
            shutil.copy2(original, destination)


def clone_tree_copy_on_write(source: str | Path, target: str | Path) -> None:
    """Clone a tree using hard links when the filesystem supports them."""

    original_root = Path(source).expanduser().resolve()
    destination_root = Path(target).expanduser().resolve()
    if original_root.is_symlink() or not original_root.is_dir():
        raise ValueError(f"copy-on-write source must be a regular directory: {original_root}")
    destination_root.mkdir(parents=True, exist_ok=True)
    for original in sorted(original_root.rglob("*")):
        if original.is_symlink():
            raise ValueError(f"copy-on-write source must not contain symlinks: {original}")
        relative = original.relative_to(original_root)
        destination = destination_root / relative
        if original.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif original.is_file():
            ContentAddressedStore.materialize(original, destination)
