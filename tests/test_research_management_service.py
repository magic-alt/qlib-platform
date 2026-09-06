from __future__ import annotations

from datetime import timedelta

import pytest

from qlib_platform.auth import Principal, ServiceTokenStore, TamperEvidentAuditLog
from qlib_platform.research.management import ResearchGovernanceStore, ResearchManagementService


def _principal(subject: str, *roles: str) -> Principal:
    return Principal(subject=subject, username=subject, roles=tuple(roles))


def test_management_service_audits_project_membership_and_grants(tmp_path) -> None:
    governance = ResearchGovernanceStore(tmp_path / "governance.sqlite")
    tokens = ServiceTokenStore(tmp_path / "tokens.sqlite")
    audit = TamperEvidentAuditLog(tmp_path / "audit.sqlite")
    service = ResearchManagementService(governance, tokens, audit)

    alice = _principal("alice", "researcher")
    project = service.create_project(alice, "alpha", display_name="Alpha")
    assert project.owner_subject == "alice"
    service.add_member(alice, "alpha", "bob", "viewer")
    assert governance.project_role("alpha", "bob") == "viewer"
    governance.bind_resource("artifact", "artifact-1", project_id="alpha", owner_subject="alice")
    service.grant_resource(
        alice,
        "alpha",
        subject="carol",
        resource_kind="artifact",
        resource_id="artifact-1",
        permission="read",
    )
    assert governance.has_direct_grant("carol", "artifact", "artifact-1", "read")

    with pytest.raises(PermissionError, match="admin or researcher"):
        service.create_project(_principal("viewer", "viewer"), "blocked", display_name="Blocked")
    with pytest.raises(PermissionError, match="membership management"):
        service.add_member(_principal("bob", "viewer"), "alpha", "carol", "viewer")
    with pytest.raises(ValueError, match="not bound"):
        service.grant_resource(
            alice,
            "alpha",
            subject="carol",
            resource_kind="artifact",
            resource_id="other",
            permission="read",
        )
    assert audit.verify().event_count >= 5
    assert any(event.outcome == "DENIED" for event in audit.events())


def test_management_service_controls_service_account_and_token_issue(tmp_path) -> None:
    governance = ResearchGovernanceStore(tmp_path / "governance.sqlite")
    tokens = ServiceTokenStore(tmp_path / "tokens.sqlite")
    audit = TamperEvidentAuditLog(tmp_path / "audit.sqlite")
    service = ResearchManagementService(governance, tokens, audit)

    admin = _principal("admin-user", "admin")
    with pytest.raises(PermissionError, match="requires admin"):
        service.create_service_account(_principal("alice", "researcher"), "svc")
    account = service.create_service_account(admin, "nightly", account_id="svc-nightly")
    assert account.owner_subject == "admin-user"
    with pytest.raises(KeyError, match="unknown service account"):
        service.issue_service_token(admin, "missing", scopes=("dataset:read",))
    with pytest.raises(PermissionError, match="owner or admin"):
        service.issue_service_token(
            _principal("mallory", "researcher"),
            account.account_id,
            scopes=("dataset:read",),
        )
    token = service.issue_service_token(
        admin,
        account.account_id,
        scopes=("dataset:read", "experiment:read"),
        ttl=timedelta(minutes=15),
    )
    credential = tokens.verify_token(token)
    assert credential.scopes == ("dataset:read", "experiment:read")
    events = audit.events()
    assert any(event.action == "service-account.create" and event.outcome == "SUCCEEDED" for event in events)
    assert any(event.action == "service-token.issue" and event.outcome == "SUCCEEDED" for event in events)
