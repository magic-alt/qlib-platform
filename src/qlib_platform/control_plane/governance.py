from __future__ import annotations

from dataclasses import dataclass

from qlib_platform.auth.audit import TamperEvidentAuditLog
from qlib_platform.auth.backend import Principal
from qlib_platform.research.management.store import ResearchGovernanceStore

_HIGH_RISK_ACTIONS = frozenset(
    {
        "release.activate",
        "benchmark.threshold.change",
        "model.promote",
        "artifact.promote",
        "manual.override",
    }
)
_ACTION_ROLES: dict[str, frozenset[str]] = {
    "release.activate": frozenset({"approver"}),
    "benchmark.threshold.change": frozenset({"approver"}),
    "model.promote": frozenset({"approver"}),
    "artifact.promote": frozenset({"approver"}),
    "manual.override": frozenset({"approver"}),
    "executor.cancel": frozenset({"operator", "approver"}),
    "executor.resume": frozenset({"operator", "approver"}),
}


def _required(value: str, name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must be non-empty")
    return normalized


@dataclass(frozen=True)
class GovernedDecision:
    project_id: str
    action: str
    target_kind: str
    target_id: str
    actor_subject: str
    approval_id: str | None


class InstitutionalActionPolicy:
    """Separate high-risk authorization surface; research CRUD grants cannot imply approval."""

    def __init__(self, governance: ResearchGovernanceStore) -> None:
        self.governance = governance

    def authorize(self, actor: Principal, project_id: str, action: str) -> bool:
        normalized = action.strip().lower()
        allowed_roles = _ACTION_ROLES.get(normalized)
        if allowed_roles is None:
            return False
        if "admin" in actor.roles:
            return True
        role = self.governance.project_role(project_id, actor.subject)
        return role in allowed_roles


class GovernedActionService:
    def __init__(self, governance: ResearchGovernanceStore, audit: TamperEvidentAuditLog) -> None:
        self.governance = governance
        self.audit = audit
        self.policy = InstitutionalActionPolicy(governance)

    def authorize(
        self,
        actor: Principal,
        project_id: str,
        *,
        action: str,
        target_kind: str,
        target_id: str,
        reason: str,
        approval_id: str | None = None,
    ) -> GovernedDecision:
        project = _required(project_id, "project_id")
        normalized_action = _required(action, "action").lower()
        target_type = _required(target_kind, "target_kind")
        target = _required(target_id, "target_id")
        justification = _required(reason, "reason")
        approval = approval_id.strip() if approval_id is not None else None
        metadata = {
            "project_id": project,
            "reason": justification,
            "approval_id": approval,
        }
        if normalized_action in _HIGH_RISK_ACTIONS and not approval:
            self.audit.append(
                actor_subject=actor.subject,
                action=f"control-plane.{normalized_action}",
                resource_kind=target_type,
                resource_id=target,
                outcome="DENIED",
                metadata=metadata,
            )
            raise PermissionError("high-risk action requires an immutable approval reference")
        if not self.policy.authorize(actor, project, normalized_action):
            self.audit.append(
                actor_subject=actor.subject,
                action=f"control-plane.{normalized_action}",
                resource_kind=target_type,
                resource_id=target,
                outcome="DENIED",
                metadata=metadata,
            )
            raise PermissionError(f"actor is not authorized for control-plane action {normalized_action!r}")
        self.audit.append(
            actor_subject=actor.subject,
            action=f"control-plane.{normalized_action}",
            resource_kind=target_type,
            resource_id=target,
            outcome="SUCCEEDED",
            metadata=metadata,
        )
        return GovernedDecision(project, normalized_action, target_type, target, actor.subject, approval)


@dataclass(frozen=True)
class DataEntitlement:
    entitlement_id: str
    provider: str
    license_id: str
    allowed_projects: frozenset[str]
    export_allowed: bool = False

    def __post_init__(self) -> None:
        _required(self.entitlement_id, "entitlement_id")
        _required(self.provider, "provider")
        _required(self.license_id, "license_id")
        if not self.allowed_projects:
            raise ValueError("allowed_projects must not be empty")


class EntitlementRegistry:
    """Immutable licensing facts attached to release/artifact metadata by entitlement reference."""

    def __init__(self) -> None:
        self._items: dict[str, DataEntitlement] = {}

    def register(self, entitlement: DataEntitlement) -> DataEntitlement:
        existing = self._items.get(entitlement.entitlement_id)
        if existing is not None and existing != entitlement:
            raise ValueError("entitlement identity is immutable")
        self._items.setdefault(entitlement.entitlement_id, entitlement)
        return self._items[entitlement.entitlement_id]

    def require_use(self, project_id: str, entitlement_id: str) -> DataEntitlement:
        entitlement = self._required(entitlement_id)
        if project_id not in entitlement.allowed_projects:
            raise PermissionError("project is not entitled to use this licensed data")
        return entitlement

    def require_export(self, project_id: str, entitlement_id: str) -> DataEntitlement:
        entitlement = self.require_use(project_id, entitlement_id)
        if not entitlement.export_allowed:
            raise PermissionError("data license forbids artifact export")
        return entitlement

    def _required(self, entitlement_id: str) -> DataEntitlement:
        try:
            return self._items[entitlement_id]
        except KeyError as exc:
            raise KeyError(f"unknown entitlement {entitlement_id!r}") from exc
