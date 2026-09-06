from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from qlib_platform.auth import (
    DirectoryIdentityConfig,
    DirectoryIdentityMapper,
    DirectoryRecord,
    FederatedIdentityConfig,
    OIDCIdentityMapper,
    Principal,
    ResearchAccessPolicy,
    ResourceRef,
    ServiceTokenStore,
    TamperEvidentAuditLog,
)
from qlib_platform.research.evidence.experiment_store import ExperimentStore
from qlib_platform.research.management import GovernedExperimentStore, ResearchGovernanceStore


def _now() -> datetime:
    return datetime(2026, 9, 6, 8, 0, tzinfo=timezone.utc)


def _principal(subject: str, *roles: str) -> Principal:
    return Principal(subject=subject, username=subject, roles=tuple(roles))


def test_oidc_mapper_maps_verified_claims_and_group_roles() -> None:
    mapper = OIDCIdentityMapper(
        FederatedIdentityConfig(
            issuer="https://id.example.test",
            audience="qlib-platform",
            group_role_mapping={
                "quant-research": ("researcher",),
                "risk-review": ("viewer", "risk-reviewer"),
            },
            default_roles=("viewer",),
        )
    )
    principal = mapper.map_verified_claims(
        {
            "iss": "https://id.example.test",
            "aud": ["other", "qlib-platform"],
            "sub": "oidc|123",
            "preferred_username": "alice",
            "groups": ["quant-research", "risk-review"],
            "exp": _now().timestamp() + 300,
            "nbf": _now().timestamp() - 5,
        },
        now=_now(),
    )
    assert principal.subject == "oidc|123"
    assert principal.username == "alice"
    assert principal.roles == ("researcher", "risk-reviewer", "viewer")


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"iss": "https://evil.example"}, "issuer"),
        ({"aud": "wrong"}, "audience"),
        ({"exp": _now().timestamp() - 3600}, "expired"),
        ({"nbf": _now().timestamp() + 3600}, "not active"),
        ({"sub": ""}, "subject"),
        ({"preferred_username": ""}, "preferred_username"),
        ({"aud": 123}, "audience"),
        ({"groups": 123}, "groups"),
        ({"exp": "not-a-number"}, "expiration"),
        ({"nbf": "not-a-number"}, "not-before"),
    ],
)
def test_oidc_mapper_fails_closed_on_invalid_claims(patch: dict[str, object], message: str) -> None:
    mapper = OIDCIdentityMapper(
        FederatedIdentityConfig(issuer="https://id.example.test", audience="qlib-platform")
    )
    claims: dict[str, object] = {
        "iss": "https://id.example.test",
        "aud": "qlib-platform",
        "sub": "oidc|123",
        "preferred_username": "alice",
        "exp": _now().timestamp() + 300,
    }
    claims.update(patch)
    with pytest.raises(PermissionError, match=message):
        mapper.map_verified_claims(claims, now=_now())


def test_federated_config_rejects_unsafe_configuration() -> None:
    with pytest.raises(ValueError, match="issuer and audience"):
        FederatedIdentityConfig(issuer="", audience="qlib-platform")
    with pytest.raises(ValueError, match="username_claim"):
        FederatedIdentityConfig(issuer="https://id.example.test", audience="qlib-platform", username_claim="")
    with pytest.raises(ValueError, match="clock_skew"):
        FederatedIdentityConfig(
            issuer="https://id.example.test", audience="qlib-platform", clock_skew_seconds=-1
        )
    mapper = OIDCIdentityMapper(
        FederatedIdentityConfig(issuer="https://id.example.test", audience="qlib-platform")
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        mapper.map_verified_claims(
            {
                "iss": "https://id.example.test",
                "aud": "qlib-platform",
                "sub": "x",
                "preferred_username": "x",
                "exp": _now().timestamp() + 10,
            },
            now=datetime(2026, 9, 6),
        )


def test_directory_mapper_supports_ldap_ad_normalization_and_disabled_fail_closed() -> None:
    mapper = DirectoryIdentityMapper(
        DirectoryIdentityConfig(
            group_role_mapping={"CN=Quant,OU=Groups": ("researcher",)},
            default_roles=("viewer",),
        )
    )
    principal = mapper.map_record(
        DirectoryRecord(
            subject="S-1-5-21-123",
            username="alice@example.test",
            groups=("CN=Quant,OU=Groups",),
        )
    )
    assert principal.roles == ("researcher", "viewer")
    with pytest.raises(PermissionError, match="disabled"):
        mapper.map_record(DirectoryRecord(subject="x", username="x", enabled=False))
    with pytest.raises(PermissionError, match="subject"):
        mapper.map_record(DirectoryRecord(subject="", username="alice"))
    with pytest.raises(PermissionError, match="username"):
        mapper.map_record(DirectoryRecord(subject="subject", username=""))


def test_governance_store_and_policy_enforce_project_roles_and_resource_scope(tmp_path) -> None:
    governance = ResearchGovernanceStore(tmp_path / "governance.sqlite")
    project = governance.create_project(
        "alpha-team",
        display_name="Alpha Team",
        owner_subject="alice",
        created_at_utc="2026-09-06T00:00:00+00:00",
    )
    assert governance.get_project("alpha-team") == project
    assert governance.project_role("alpha-team", "alice") == "owner"
    governance.add_member("alpha-team", "bob", "researcher")
    governance.add_member("alpha-team", "carol", "viewer")
    governance.bind_resource("experiment", "exp-1", project_id="alpha-team", owner_subject="alice")
    policy = ResearchAccessPolicy(governance)

    assert policy.authorize(_principal("alice"), ResourceRef("experiment", "exp-1"), "manage")
    assert policy.authorize(_principal("bob"), ResourceRef("experiment", "exp-1"), "write")
    assert policy.authorize(_principal("carol"), ResourceRef("experiment", "exp-1"), "read")
    assert not policy.authorize(_principal("carol"), ResourceRef("experiment", "exp-1"), "write")
    assert not policy.authorize(_principal("mallory"), ResourceRef("experiment", "exp-1"), "read")

    governance.grant("mallory", "experiment", "exp-1", "read")
    assert policy.authorize(_principal("mallory"), ResourceRef("experiment", "exp-1"), "read")
    governance.revoke_grant("mallory", "experiment", "exp-1", "read")
    assert not policy.authorize(_principal("mallory"), ResourceRef("experiment", "exp-1"), "read")

    admin = _principal("root", "admin")
    assert policy.authorize(admin, ResourceRef("experiment", "unbound"), "manage")
    assert not policy.authorize(admin, ResourceRef("experiment", "exp-1"), "promotion:approve")
    assert not policy.authorize(admin, ResourceRef("experiment", "exp-1"), "execution:submit")
    assert not policy.authorize(admin, ResourceRef("experiment", "exp-1"), "unknown")
    with pytest.raises(PermissionError, match="not authorized"):
        policy.require(_principal("carol"), ResourceRef("experiment", "exp-1"), "write")


def test_governance_membership_ownership_binding_and_validation(tmp_path) -> None:
    governance = ResearchGovernanceStore(tmp_path / "governance.sqlite")
    governance.create_project("p", display_name="Project", owner_subject="alice")
    with pytest.raises(sqlite3.IntegrityError):
        governance.create_project("p", display_name="Duplicate", owner_subject="alice")
    with pytest.raises(ValueError, match="unsupported project role"):
        governance.add_member("p", "bob", "superuser")
    with pytest.raises(KeyError, match="unknown research project"):
        governance.add_member("missing", "bob", "viewer")
    with pytest.raises(ValueError, match="transfer_ownership"):
        governance.add_member("p", "bob", "owner")

    governance.add_member("p", "bob", "maintainer")
    governance.transfer_ownership("p", "bob")
    assert governance.project_role("p", "bob") == "owner"
    assert governance.project_role("p", "alice") == "maintainer"
    with pytest.raises(ValueError, match="owner cannot be removed"):
        governance.remove_member("p", "bob")
    governance.remove_member("p", "alice")
    assert governance.project_role("p", "alice") is None
    with pytest.raises(KeyError, match="unknown research project"):
        governance.remove_member("missing", "x")
    with pytest.raises(KeyError, match="unknown research project"):
        governance.transfer_ownership("missing", "x")

    governance.bind_resource("dataset", "ds-1", project_id="p", owner_subject="bob")
    assert governance.resource_project("dataset", "ds-1") == "p"
    governance.create_project("other", display_name="Other", owner_subject="carol")
    with pytest.raises(ValueError, match="already bound"):
        governance.bind_resource("dataset", "ds-1", project_id="other", owner_subject="carol")
    with pytest.raises(ValueError, match="bindable resource"):
        governance.bind_resource("project", "p", project_id="p", owner_subject="bob")
    with pytest.raises(KeyError, match="unknown research project"):
        governance.bind_resource("artifact", "a", project_id="missing", owner_subject="bob")
    with pytest.raises(ValueError, match="governed resource kind"):
        governance.grant("x", "broker-order", "1", "read")
    with pytest.raises(ValueError, match="research permission"):
        governance.grant("x", "artifact", "1", "execute")


@pytest.mark.parametrize(
    ("kind", "resource_id", "project_id"),
    [("bad", "x", None), ("experiment", "", None), ("artifact", "a", "")],
)
def test_resource_ref_validation(kind: str, resource_id: str, project_id: str | None) -> None:
    with pytest.raises(ValueError):
        ResourceRef(kind, resource_id, project_id)


def test_tamper_evident_audit_log_verifies_chain_and_external_head(tmp_path) -> None:
    audit = TamperEvidentAuditLog(tmp_path / "audit.sqlite")
    first = audit.append(
        actor_subject="alice",
        action="project.create",
        resource_kind="project",
        resource_id="alpha",
        outcome="succeeded",
        metadata={"member_count": 1},
        created_at_utc="2026-09-06T00:00:00+00:00",
        event_id="event-1",
    )
    second = audit.append(
        actor_subject="bob",
        action="experiment.read",
        resource_kind="experiment",
        resource_id="exp-1",
        outcome="denied",
        created_at_utc="2026-09-06T00:01:00+00:00",
        event_id="event-2",
    )
    assert first.sequence == 1
    assert second.sequence == 2
    assert second.previous_hash == first.event_hash
    verification = audit.verify(expected_head_hash=second.event_hash)
    assert verification.event_count == 2
    assert verification.head_hash == second.event_hash
    assert [event.event_id for event in audit.events()] == ["event-1", "event-2"]
    with pytest.raises(ValueError, match="external anchor"):
        audit.verify(expected_head_hash="0" * 64)

    with sqlite3.connect(audit.database) as connection:
        connection.execute("UPDATE audit_events SET action='tampered' WHERE sequence=1")
    with pytest.raises(ValueError, match="hash mismatch"):
        audit.verify()


def test_audit_log_rejects_sensitive_or_malformed_events(tmp_path) -> None:
    audit = TamperEvidentAuditLog(tmp_path / "audit.sqlite")
    with pytest.raises(ValueError, match="non-empty"):
        audit.append(
            actor_subject="",
            action="read",
            resource_kind="artifact",
            resource_id="x",
            outcome="ok",
        )
    with pytest.raises(ValueError, match="sensitive"):
        audit.append(
            actor_subject="alice",
            action="read",
            resource_kind="artifact",
            resource_id="x",
            outcome="ok",
            metadata={"access_token": "must-never-be-logged"},
        )
    with pytest.raises(ValueError, match="JSON serializable"):
        audit.append(
            actor_subject="alice",
            action="read",
            resource_kind="artifact",
            resource_id="x",
            outcome="ok",
            metadata={"bad": object()},
        )


def test_service_token_full_lifecycle_is_hash_only_and_scope_bounded(tmp_path) -> None:
    store = ServiceTokenStore(tmp_path / "tokens.sqlite")
    account = store.create_service_account("nightly-research", owner_subject="alice", account_id="svc-1")
    assert store.get_service_account("svc-1") == account
    token = store.issue_token(
        account.account_id,
        scopes=("experiment:read", "artifact:write", "experiment:read"),
        ttl=timedelta(hours=1),
        now=_now(),
    )
    assert token.startswith("qpt_")
    credential = store.verify_token(token, now=_now() + timedelta(minutes=5))
    assert credential.principal.subject == "service:svc-1"
    assert credential.principal.roles == ("service-account",)
    assert credential.scopes == ("artifact:write", "experiment:read")
    assert credential.allows("experiment:read")
    assert not credential.allows("execution:submit")

    public, secret = token.split(".", 1)
    token_id = public.removeprefix("qpt_")
    with sqlite3.connect(store.database) as connection:
        stored = connection.execute(
            "SELECT secret_sha256 FROM service_tokens WHERE token_id=?", (token_id,)
        ).fetchone()
    assert stored is not None
    assert secret not in str(stored[0])

    with pytest.raises(PermissionError, match="invalid service token"):
        store.verify_token(f"{public}.wrong-secret", now=_now())
    with pytest.raises(PermissionError, match="expired"):
        store.verify_token(token, now=_now() + timedelta(hours=2))

    replacement = store.rotate_token(token, ttl=timedelta(hours=2), now=_now() + timedelta(minutes=10))
    with pytest.raises(PermissionError, match="revoked"):
        store.verify_token(token, now=_now() + timedelta(minutes=11))
    assert store.verify_token(replacement, now=_now() + timedelta(minutes=11)).scopes == credential.scopes
    replacement_id = replacement.split(".", 1)[0].removeprefix("qpt_")
    store.revoke_token(replacement_id, now=_now() + timedelta(minutes=12))
    with pytest.raises(PermissionError, match="revoked"):
        store.verify_token(replacement, now=_now() + timedelta(minutes=13))


def test_service_token_validation_and_account_disable(tmp_path) -> None:
    store = ServiceTokenStore(tmp_path / "tokens.sqlite")
    with pytest.raises(ValueError, match="name and owner_subject"):
        store.create_service_account("", owner_subject="alice")
    account = store.create_service_account("svc", owner_subject="alice")
    with pytest.raises(ValueError, match="at least one"):
        store.issue_token(account.account_id, scopes=())
    with pytest.raises(ValueError, match="cannot acquire"):
        store.issue_token(account.account_id, scopes=("execution:submit",))
    with pytest.raises(ValueError, match="unknown service token scopes"):
        store.issue_token(account.account_id, scopes=("root:everything",))
    with pytest.raises(ValueError, match="ttl must be positive"):
        store.issue_token(account.account_id, scopes=("dataset:read",), ttl=timedelta(0))
    with pytest.raises(PermissionError, match="unavailable"):
        store.issue_token("missing", scopes=("dataset:read",))
    with pytest.raises(PermissionError, match="invalid service token"):
        store.verify_token("not-a-token")
    with pytest.raises(KeyError, match="unknown service token"):
        store.revoke_token("missing")
    with pytest.raises(KeyError, match="unknown service account"):
        store.disable_service_account("missing")

    token = store.issue_token(account.account_id, scopes=("dataset:read",), now=_now())
    store.disable_service_account(account.account_id)
    assert store.get_service_account(account.account_id) is not None
    assert not store.get_service_account(account.account_id).enabled  # type: ignore[union-attr]
    with pytest.raises(PermissionError, match="invalid or revoked"):
        store.verify_token(token, now=_now() + timedelta(minutes=1))
    with pytest.raises(PermissionError, match="unavailable"):
        store.issue_token(account.account_id, scopes=("dataset:read",))


def test_governed_experiment_store_delegates_existing_store_and_audits(tmp_path) -> None:
    governance = ResearchGovernanceStore(tmp_path / "governance.sqlite")
    governance.create_project("alpha", display_name="Alpha", owner_subject="alice")
    governance.add_member("alpha", "bob", "researcher")
    governance.add_member("alpha", "carol", "viewer")
    audit = TamperEvidentAuditLog(tmp_path / "audit.sqlite")

    with ExperimentStore(tmp_path / "experiments.duckdb") as experiments:
        governed = GovernedExperimentStore(experiments, governance, audit)
        governed.register_experiment(
            _principal("alice"),
            "alpha",
            "exp-1",
            status="COMPLETED",
            dataset_id="ds-1",
            git_sha="abc123",
        )
        governed.log_metrics(_principal("bob"), "exp-1", {"ic": 0.03, "rank_ic": 0.04})
        governed.register_artifact(
            _principal("bob"),
            "exp-1",
            kind="signal",
            uri="artifacts/signal.parquet",
            sha256="a" * 64,
            metadata={"rows": 10},
        )
        experiment = governed.get_experiment(_principal("carol"), "exp-1")
        assert experiment is not None
        assert experiment["experiment_id"] == "exp-1"
        assert {item["metric_name"] for item in experiment["metrics"]} == {"ic", "rank_ic"}
        listed = governed.list_project_experiments(_principal("carol"), "alpha")
        assert listed["experiment_id"].tolist() == ["exp-1"]

        with pytest.raises(PermissionError, match="not authorized"):
            governed.log_metrics(_principal("carol"), "exp-1", {"ic": 0.99})
        with pytest.raises(PermissionError, match="not authorized"):
            governed.get_experiment(_principal("mallory"), "exp-1")
        with pytest.raises(PermissionError, match="not authorized"):
            governed.register_experiment(_principal("mallory"), "alpha", "exp-2")

    events = audit.events()
    assert any(event.outcome == "DENIED" for event in events)
    assert any(event.action == "artifact.register" and event.outcome == "SUCCEEDED" for event in events)
    assert audit.verify().event_count == len(events)


def test_governed_store_filters_multiple_projects_and_fails_closed_on_unbound_resource(tmp_path) -> None:
    governance = ResearchGovernanceStore(tmp_path / "governance.sqlite")
    governance.create_project("a", display_name="A", owner_subject="alice")
    governance.create_project("b", display_name="B", owner_subject="alice")
    audit = TamperEvidentAuditLog(tmp_path / "audit.sqlite")
    with ExperimentStore(tmp_path / "experiments.duckdb") as experiments:
        governed = GovernedExperimentStore(experiments, governance, audit)
        governed.register_experiment(_principal("alice"), "a", "a-exp")
        governed.register_experiment(_principal("alice"), "b", "b-exp")
        assert governed.list_project_experiments(_principal("alice"), "a")["experiment_id"].tolist() == [
            "a-exp"
        ]
        experiments.register_experiment("legacy-unbound")
        with pytest.raises(PermissionError, match="not authorized"):
            governed.get_experiment(_principal("alice"), "legacy-unbound")
