from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Principal:
    subject: str
    username: str
    roles: tuple[str, ...]


class AuthBackend(Protocol):
    def authenticate(self, username: str, password: str) -> Principal: ...

    def verify(self, token: str) -> Principal: ...

    def authorize(self, principal: Principal, permission: str) -> bool: ...
