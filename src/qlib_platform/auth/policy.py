from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from qlib_platform.auth.backend import Principal

RESEARCH_RESOURCE_KINDS = frozenset({"project", "experiment", "dataset", "artifact"})
RESEARCH_PERMISSIONS = frozenset({"read", "write", "run", "manage", "share"})
_FORBIDDEN_PERMISSION_PREFIXES = ("promotion:", "execution:", "broker:", "oms:")

_PROJECT_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "owner": frozenset({"read", "write", "run", "manage", "share"}),
    "maintainer": frozenset({"read", "write", "run", "share"}),
    "researcher": frozenset({"read", "write", "run"}),
    "viewer": frozenset({"read"}),
}


@dataclass(frozen=True)
class ResourceRef:
    kind: str
    resource_id: str
    project_id: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in RESEARCH_RESOURCE_KINDS:
            raise ValueError(f"unsupported governed resource kind {self.kind!r}")
        if not self.resource_id.strip():
            raise ValueError("resource_id must be non-empty")
        if self.project_id is not None and not self.project_id.strip():
            raise ValueError("project_id must be non-empty when supplied")


class ResearchAccessFacts(Protocol):
    def project_role(self, project_id: str, subject: str) -> str | None: ...

    def resource_project(self, resource_kind: str, resource_id: str) -> str | None: ...

    def has_direct_grant(
        self,
        subject: str,
        resource_kind: str,
        resource_id: str,
        permission: str,
    ) -> bool: ...


class ResearchAccessPolicy:
    """Deny-by-default RBAC/resource-scope policy for research management only.

    Model-promotion authority and broker/execution permissions are intentionally outside this
    policy. Even a global ``admin`` Principal cannot acquire those permissions here.
    """

    def __init__(self, facts: ResearchAccessFacts):
        self.facts = facts

    def authorize(self, principal: Principal, resource: ResourceRef, permission: str) -> bool:
        normalized = permission.strip().lower()
        if any(normalized.startswith(prefix) for prefix in _FORBIDDEN_PERMISSION_PREFIXES):
            return False
        if normalized not in RESEARCH_PERMISSIONS:
            return False
        if "admin" in principal.roles:
            return True
        if self.facts.has_direct_grant(
            principal.subject,
            resource.kind,
            resource.resource_id,
            normalized,
        ):
            return True
        project_id = resource.project_id
        if project_id is None:
            project_id = (
                resource.resource_id
                if resource.kind == "project"
                else self.facts.resource_project(resource.kind, resource.resource_id)
            )
        if project_id is None:
            return False
        role = self.facts.project_role(project_id, principal.subject)
        return normalized in _PROJECT_ROLE_PERMISSIONS.get(role or "", frozenset())

    def require(self, principal: Principal, resource: ResourceRef, permission: str) -> None:
        if not self.authorize(principal, resource, permission):
            raise PermissionError(
                f"{principal.subject!r} is not authorized for {permission!r} on "
                f"{resource.kind}:{resource.resource_id}"
            )
