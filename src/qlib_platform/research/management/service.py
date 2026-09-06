from __future__ import annotations

from datetime import timedelta

from qlib_platform.auth.audit import TamperEvidentAuditLog
from qlib_platform.auth.backend import Principal
from qlib_platform.auth.policy import ResourceRef, ResearchAccessPolicy
from qlib_platform.auth.tokens import ServiceAccount, ServiceTokenStore
from qlib_platform.research.management.store import ResearchGovernanceStore, ResearchProject


class ResearchManagementService:
    """Audited administrative façade for enterprise research governance operations."""

    def __init__(
        self,
        governance: ResearchGovernanceStore,
        tokens: ServiceTokenStore,
        audit: TamperEvidentAuditLog,
    ) -> None:
        self.governance = governance
        self.tokens = tokens
        self.audit = audit
        self.policy = ResearchAccessPolicy(governance)

    def create_project(
        self,
        actor: Principal,
        project_id: str,
        *,
        display_name: str,
    ) -> ResearchProject:
        if not ({"admin", "researcher"} & set(actor.roles)):
            self.audit.append(
                actor_subject=actor.subject,
                action="project.create",
                resource_kind="project",
                resource_id=project_id,
                outcome="DENIED",
            )
            raise PermissionError("project creation requires admin or researcher role")
        project = self.governance.create_project(
            project_id,
            display_name=display_name,
            owner_subject=actor.subject,
        )
        self.audit.append(
            actor_subject=actor.subject,
            action="project.create",
            resource_kind="project",
            resource_id=project_id,
            outcome="SUCCEEDED",
        )
        return project

    def add_member(self, actor: Principal, project_id: str, subject: str, role: str) -> None:
        resource = ResourceRef("project", project_id)
        if not self.policy.authorize(actor, resource, "manage"):
            self.audit.append(
                actor_subject=actor.subject,
                action="project.member.add",
                resource_kind="project",
                resource_id=project_id,
                outcome="DENIED",
                metadata={"member_subject": subject, "role": role},
            )
            raise PermissionError("project membership management requires project owner/manage access")
        self.governance.add_member(project_id, subject, role)
        self.audit.append(
            actor_subject=actor.subject,
            action="project.member.add",
            resource_kind="project",
            resource_id=project_id,
            outcome="SUCCEEDED",
            metadata={"member_subject": subject, "role": role},
        )

    def grant_resource(
        self,
        actor: Principal,
        project_id: str,
        *,
        subject: str,
        resource_kind: str,
        resource_id: str,
        permission: str,
    ) -> None:
        project = ResourceRef("project", project_id)
        self.policy.require(actor, project, "manage")
        bound_project = self.governance.resource_project(resource_kind, resource_id)
        if resource_kind != "project" and bound_project != project_id:
            raise ValueError("resource is not bound to the managed project")
        self.governance.grant(subject, resource_kind, resource_id, permission)
        self.audit.append(
            actor_subject=actor.subject,
            action="resource.grant",
            resource_kind=resource_kind,
            resource_id=resource_id,
            outcome="SUCCEEDED",
            metadata={"project_id": project_id, "subject": subject, "permission": permission},
        )

    def create_service_account(
        self,
        actor: Principal,
        name: str,
        *,
        account_id: str | None = None,
    ) -> ServiceAccount:
        if "admin" not in actor.roles:
            self.audit.append(
                actor_subject=actor.subject,
                action="service-account.create",
                resource_kind="project",
                resource_id="enterprise-auth",
                outcome="DENIED",
            )
            raise PermissionError("service-account creation requires admin role")
        account = self.tokens.create_service_account(
            name,
            owner_subject=actor.subject,
            account_id=account_id,
        )
        self.audit.append(
            actor_subject=actor.subject,
            action="service-account.create",
            resource_kind="project",
            resource_id="enterprise-auth",
            outcome="SUCCEEDED",
            metadata={"account_id": account.account_id, "name": account.name},
        )
        return account

    def issue_service_token(
        self,
        actor: Principal,
        account_id: str,
        *,
        scopes: tuple[str, ...],
        ttl: timedelta = timedelta(days=30),
    ) -> str:
        account = self.tokens.get_service_account(account_id)
        if account is None:
            raise KeyError(f"unknown service account {account_id!r}")
        if actor.subject != account.owner_subject and "admin" not in actor.roles:
            self.audit.append(
                actor_subject=actor.subject,
                action="service-token.issue",
                resource_kind="project",
                resource_id="enterprise-auth",
                outcome="DENIED",
                metadata={"account_id": account_id},
            )
            raise PermissionError("service token issue requires account owner or admin")
        token = self.tokens.issue_token(account_id, scopes=scopes, ttl=ttl)
        token_id = token.split(".", 1)[0].removeprefix("qpt_")
        self.audit.append(
            actor_subject=actor.subject,
            action="service-token.issue",
            resource_kind="project",
            resource_id="enterprise-auth",
            outcome="SUCCEEDED",
            metadata={"account_id": account_id, "token_id": token_id, "scopes": list(scopes)},
        )
        return token
