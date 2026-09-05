from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, TypeAlias


QlibConfig: TypeAlias = Mapping[str, Any] | str | Path | object


@dataclass(frozen=True)
class QlibObjectSpec:
    """Serializable description of an upstream Qlib object.

    This intentionally mirrors Qlib's ``init_instance_by_config`` contract instead
    of introducing a platform-owned allowlist. Any importable class is valid.
    """

    class_name: str
    module_path: str | None = None
    kwargs: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "QlibObjectSpec":
        class_name = str(value.get("class", "")).strip()
        if not class_name:
            raise ValueError("Qlib object config requires a non-empty 'class'")
        module = value.get("module_path")
        module_path = None if module is None else str(module).strip() or None
        kwargs = value.get("kwargs", {})
        if not isinstance(kwargs, Mapping):
            raise TypeError("Qlib object config 'kwargs' must be a mapping")
        return cls(class_name=class_name, module_path=module_path, kwargs=dict(kwargs))

    def to_config(self) -> dict[str, Any]:
        config: dict[str, Any] = {"class": self.class_name, "kwargs": dict(self.kwargs)}
        if self.module_path:
            config["module_path"] = self.module_path
        return config


def as_qlib_config(config: QlibConfig | QlibObjectSpec) -> QlibConfig:
    return config.to_config() if isinstance(config, QlibObjectSpec) else config
