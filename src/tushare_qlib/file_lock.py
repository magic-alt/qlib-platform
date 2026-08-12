from __future__ import annotations

import os
from contextlib import AbstractContextManager
from importlib import import_module
from pathlib import Path
from typing import Any


def _platform_lock_module(name: str) -> Any:
    """Load a platform-specific lock module without cross-platform type coupling."""

    return import_module(name)


class FileLock(AbstractContextManager["FileLock"]):
    """Cross-platform advisory lock without import-time platform coupling."""

    def __init__(self, path: str | Path, *, blocking: bool = True, unavailable_message: str = "file lock is already held") -> None:
        self.path = Path(path)
        self.blocking = blocking
        self.unavailable_message = unavailable_message
        self.handle: Any = None

    def __enter__(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        self.handle.seek(0)
        self.handle.write(b"0")
        self.handle.flush()
        try:
            if os.name == "nt":
                msvcrt = _platform_lock_module("msvcrt")

                self.handle.seek(0)
                mode = msvcrt.LK_LOCK if self.blocking else msvcrt.LK_NBLCK
                msvcrt.locking(self.handle.fileno(), mode, 1)
            else:  # pragma: no cover - covered by Linux CI.
                fcntl = _platform_lock_module("fcntl")

                flags = fcntl.LOCK_EX | (0 if self.blocking else fcntl.LOCK_NB)
                fcntl.flock(self.handle.fileno(), flags)
        except OSError as exc:
            self.handle.close()
            self.handle = None
            raise RuntimeError(self.unavailable_message) from exc
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.handle is None:
            return
        try:
            if os.name == "nt":
                msvcrt = _platform_lock_module("msvcrt")

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover - covered by Linux CI.
                fcntl = _platform_lock_module("fcntl")

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None
