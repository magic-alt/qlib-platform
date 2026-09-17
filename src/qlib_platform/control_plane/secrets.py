from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Protocol


def _required(value: str, name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must be non-empty")
    return normalized


@dataclass(frozen=True)
class SecretRef:
    """Persistable secret identity. The secret value is intentionally not part of this object."""

    provider: str
    project_id: str
    name: str
    version: str = "current"

    def __post_init__(self) -> None:
        for field_name in ("provider", "project_id", "name", "version"):
            _required(str(getattr(self, field_name)), field_name)

    def as_dict(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "project_id": self.project_id,
            "name": self.name,
            "version": self.version,
        }


class SecretProvider(Protocol):
    provider: str

    def resolve(self, ref: SecretRef, *, project_id: str) -> str: ...


class MemorySecretProvider:
    """Test/reference provider whose values never enter persisted run metadata."""

    provider = "memory"

    def __init__(self) -> None:
        self._values: dict[tuple[str, str, str], str] = {}

    def put(self, ref: SecretRef, value: str) -> None:
        self._check_ref(ref, ref.project_id)
        if not value:
            raise ValueError("secret value must be non-empty")
        self._values[(ref.project_id, ref.name, ref.version)] = value

    def resolve(self, ref: SecretRef, *, project_id: str) -> str:
        self._check_ref(ref, project_id)
        try:
            return self._values[(project_id, ref.name, ref.version)]
        except KeyError as exc:
            raise KeyError(f"secret reference {ref.name!r} is unavailable for project {project_id!r}") from exc

    def _check_ref(self, ref: SecretRef, project_id: str) -> None:
        if ref.provider != self.provider:
            raise ValueError(f"secret ref provider {ref.provider!r} does not match {self.provider!r}")
        if ref.project_id != project_id:
            raise PermissionError("cross-project secret resolution is forbidden")


class EnvSecretProvider:
    """Explicit ref-to-environment mapping; run metadata stores only ``SecretRef``."""

    provider = "env"

    def __init__(self, bindings: Mapping[tuple[str, str, str], str]) -> None:
        self._bindings = dict(bindings)

    def resolve(self, ref: SecretRef, *, project_id: str) -> str:
        if ref.provider != self.provider:
            raise ValueError(f"secret ref provider {ref.provider!r} does not match {self.provider!r}")
        if ref.project_id != project_id:
            raise PermissionError("cross-project secret resolution is forbidden")
        env_name = self._bindings.get((project_id, ref.name, ref.version))
        if env_name is None:
            raise KeyError(f"secret reference {ref.name!r} is not bound for project {project_id!r}")
        value = os.environ.get(env_name)
        if not value:
            raise KeyError(f"environment binding {env_name!r} is unset")
        return value


class KeyringSecretProvider:
    """Optional desktop/local provider without making keyring a core dependency."""

    provider = "keyring"

    def __init__(self, *, service_prefix: str = "qlib-platform") -> None:
        self.service_prefix = _required(service_prefix, "service_prefix")

    def resolve(self, ref: SecretRef, *, project_id: str) -> str:
        if ref.provider != self.provider:
            raise ValueError(f"secret ref provider {ref.provider!r} does not match {self.provider!r}")
        if ref.project_id != project_id:
            raise PermissionError("cross-project secret resolution is forbidden")
        try:
            import keyring
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("keyring secret provider requires the optional 'keyring' package") from exc
        value = keyring.get_password(
            f"{self.service_prefix}:{project_id}",
            f"{ref.name}:{ref.version}",
        )
        if not value:
            raise KeyError(f"keyring secret reference {ref.name!r} is unavailable")
        return str(value)
