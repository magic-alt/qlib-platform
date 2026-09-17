from __future__ import annotations

import hashlib
from dataclasses import fields, replace

import pytest

from qlib_platform.auth import Principal, ResearchAccessPolicy, ResourceRef, TamperEvidentAuditLog
from qlib_platform.control_plane import (
    ArtifactMetadata,
    DataEntitlement,
    EntitlementRegistry,
    EnvSecretProvider,
    ExecutionBudget,
    ExecutionOutcome,
    ExecutionRequest,
    ExecutionStatus,
    FakeWorkerExecutionBackend,
    FileSystemObjectStore,
    GovernedActionService,
    LocalExecutionBackend,
    MemoryMetadataStore,
    MemoryObjectStore,
    MemorySecretProvider,
    ProjectQuota,
    ProjectWorkspace,
    SecretRef,
    SqliteMetadataStore,
)
from qlib_platform.research.management.store import ResearchGovernanceStore


def _principal(subject: str, *roles: str) -> Principal:
    return Principal(subject=subject, username=subject, roles=tuple(roles))


def _governance(tmp_path):
    store = ResearchGovernanceStore(tmp_path / "governance.sqlite")
    store.create_project("alpha", display_name="Alpha", owner_subject="owner-a")
    store.create_project("beta", display_name="Beta", owner_subject="owner-b")
    return store


def _request(project_id: str = "alpha") -> ExecutionRequest:
    return ExecutionRequest(
        project_id=project_id,
        operation="research.run",
        descriptor_ref="git:research/alpha158@v1",
        config_ref="artifact:config-001",
        artifact_refs=("dataset:release-001",),
        secret_refs=(SecretRef("memory", project_id, "market-data-token", "v1"),),
        budget=ExecutionBudget(cpu_cores=2, memory_gb=4, runtime_seconds=1800),
        matrix_jobs=4,
    )


def test_project_roles_are_isolated_and_high_risk_permissions_are_not_research_grants(tmp_path):
    store = _governance(tmp_path)
    store.add_member("alpha", "operator-a", "operator")
    store.add_member("alpha", "approver-a", "approver")
    policy = ResearchAccessPolicy(store)

    operator = _principal("operator-a", "researcher")
    approver = _principal("approver-a", "researcher")
    alpha = ResourceRef("project", "alpha")
    beta = ResourceRef("project", "beta")

    assert policy.authorize(operator, alpha, "read")
    assert policy.authorize(operator, alpha, "run")
    assert not policy.authorize(operator, alpha, "write")
    assert policy.authorize(approver, alpha, "read")
    assert not policy.authorize(approver, alpha, "manage")
    assert not policy.authorize(operator, beta, "read")
    assert not policy.authorize(operator, alpha, "promotion:model")


def test_secret_refs_store_no_value_and_fail_closed_across_projects(monkeypatch):
    alpha_ref = SecretRef("memory", "alpha", "token", "v1")
    provider = MemorySecretProvider()
    provider.put(alpha_ref, "super-secret")

    assert provider.resolve(alpha_ref, project_id="alpha") == "super-secret"
    assert "super-secret" not in repr(alpha_ref)
    assert "value" not in alpha_ref.as_dict()
    with pytest.raises(PermissionError):
        provider.resolve(alpha_ref, project_id="beta")

    env_ref = SecretRef("env", "alpha", "provider-token", "v1")
    env = EnvSecretProvider({("alpha", "provider-token", "v1"): "QLIB_TEST_PROVIDER_TOKEN"})
    monkeypatch.setenv("QLIB_TEST_PROVIDER_TOKEN", "from-env")
    assert env.resolve(env_ref, project_id="alpha") == "from-env"
    monkeypatch.delenv("QLIB_TEST_PROVIDER_TOKEN")
    with pytest.raises(KeyError):
        env.resolve(env_ref, project_id="alpha")


@pytest.mark.parametrize("backend_kind", ["memory", "filesystem"])
def test_object_store_contract_is_content_addressed_and_project_scoped(tmp_path, backend_kind):
    store = MemoryObjectStore() if backend_kind == "memory" else FileSystemObjectStore(tmp_path / "objects")
    payload = b"immutable-research-artifact"
    ref = store.put("alpha", payload, media_type="application/json")
    same = store.put("alpha", payload, media_type="application/json")

    assert ref.digest == hashlib.sha256(payload).hexdigest()
    assert ref == same
    assert store.get("alpha", ref) == payload
    with pytest.raises(PermissionError):
        store.get("beta", ref)


def test_filesystem_object_store_rejects_symlink_payload_path(tmp_path):
    store = FileSystemObjectStore(tmp_path / "objects")
    payload = b"artifact"
    ref = store.put("alpha", payload)
    object_path = store.root / "alpha" / "objects" / "sha256" / ref.digest[:2] / ref.digest
    object_path.unlink()
    target = tmp_path / "outside.bin"
    target.write_bytes(payload)
    try:
        object_path.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable on this platform")
    with pytest.raises(PermissionError):
        store.get("alpha", ref)


def test_project_workspace_blocks_absolute_and_parent_traversal(tmp_path):
    workspace = ProjectWorkspace(tmp_path / "workspaces")
    resolved = workspace.resolve("alpha", "runs/run-1/state.json")
    assert resolved.is_relative_to((tmp_path / "workspaces" / "alpha").resolve())
    with pytest.raises(PermissionError):
        workspace.resolve("alpha", "../beta/state.json")
    with pytest.raises(PermissionError):
        workspace.resolve("alpha", tmp_path / "absolute.json")


@pytest.mark.parametrize("backend_kind", ["memory", "sqlite"])
def test_metadata_backend_contract_keeps_bytes_outside_metadata_and_refcounts(tmp_path, backend_kind):
    store = (
        MemoryMetadataStore()
        if backend_kind == "memory"
        else SqliteMetadataStore(tmp_path / "metadata.sqlite")
    )
    object_store = MemoryObjectStore()
    ref = object_store.put("alpha", b"large-bytes-live-in-object-store")
    metadata = ArtifactMetadata(
        project_id="alpha",
        artifact_id="artifact-001",
        object_ref=ref,
        manifest_digest=hashlib.sha256(b"manifest").hexdigest(),
        entitlement_id="license-cn-equity",
    )

    assert store.register(metadata) == metadata
    assert store.get("alpha", "artifact-001") == metadata
    assert store.get("beta", "artifact-001") is None
    assert store.add_reference("alpha", "artifact-001").ref_count == 2
    assert store.release_reference("alpha", "artifact-001").ref_count == 1
    with pytest.raises(ValueError):
        store.register(replace(metadata, manifest_digest=hashlib.sha256(b"other").hexdigest()))


def test_execution_business_identity_matches_local_and_worker_and_duplicate_delivery_is_idempotent():
    calls = {"local": 0, "worker": 0}
    request = _request()

    def local_handler(job: ExecutionRequest) -> ExecutionOutcome:
        calls["local"] += 1
        assert job.business_id == request.business_id
        return ExecutionOutcome(("artifact-result",), hashlib.sha256(b"manifest").hexdigest())

    def worker_handler(job: ExecutionRequest) -> ExecutionOutcome:
        calls["worker"] += 1
        assert job.business_id == request.business_id
        return ExecutionOutcome(("artifact-result",), hashlib.sha256(b"manifest").hexdigest())

    local = LocalExecutionBackend(local_handler)
    worker = FakeWorkerExecutionBackend(worker_handler)
    local_record = local.submit(request)
    queued = worker.submit(request)
    worker_record = worker.deliver(request, delivery_id="delivery-1")
    duplicate = worker.deliver(request, delivery_id="delivery-2")

    assert queued.business_id == request.business_id
    assert local_record.business_id == worker_record.business_id == request.business_id
    assert local_record.artifact_ids == worker_record.artifact_ids == ("artifact-result",)
    assert local_record.manifest_digest == worker_record.manifest_digest
    assert duplicate == worker_record
    assert calls == {"local": 1, "worker": 1}
    assert local.submit(request) == local_record
    assert calls["local"] == 1


def test_worker_cancel_and_resume_have_deterministic_semantics():
    calls = 0
    request = _request()

    def handler(_: ExecutionRequest) -> ExecutionOutcome:
        nonlocal calls
        calls += 1
        return ExecutionOutcome(("artifact-result",), hashlib.sha256(b"manifest").hexdigest())

    worker = FakeWorkerExecutionBackend(handler)
    assert worker.submit(request).status is ExecutionStatus.QUEUED
    assert worker.cancel(request).status is ExecutionStatus.CANCELLED
    assert worker.deliver(request, delivery_id="ignored-after-cancel").status is ExecutionStatus.CANCELLED
    assert calls == 0
    assert worker.resume(request).status is ExecutionStatus.QUEUED
    completed = worker.deliver(request, delivery_id="delivery-after-resume")
    assert completed.status is ExecutionStatus.SUCCEEDED
    assert completed.attempts == 1
    assert calls == 1
    assert worker.resume(request) == completed


def test_executor_resource_governance_fails_closed_on_budget_and_concurrency():
    request = _request()
    outcome = ExecutionOutcome(("artifact",), hashlib.sha256(b"manifest").hexdigest())
    tiny = ProjectQuota(max_concurrency=1, max_matrix_jobs=2, max_cpu_hours_per_run=1, max_memory_gb=2)
    worker = FakeWorkerExecutionBackend(lambda _: outcome, quota=tiny)
    with pytest.raises(RuntimeError, match="matrix"):
        worker.submit(request)

    allowed = replace(
        request, matrix_jobs=1, budget=ExecutionBudget(cpu_cores=1, memory_gb=1, runtime_seconds=60)
    )
    other = replace(allowed, descriptor_ref="git:research/other@v1")
    worker.submit(allowed)
    with pytest.raises(RuntimeError, match="concurrency"):
        worker.submit(other)


def test_execution_envelope_has_no_raw_shell_or_python_payload_surface():
    names = {field.name for field in fields(ExecutionRequest)}
    assert not {"command", "shell", "python", "script", "code", "payload"} & names
    with pytest.raises(ValueError):
        ExecutionRequest("alpha", "research.run", "", "artifact:config")
    with pytest.raises(ValueError):
        ExecutionRequest(
            "alpha",
            "research.run",
            "git:research/v1",
            "artifact:config",
            secret_refs=(SecretRef("memory", "beta", "token"),),
        )


def test_high_risk_actions_require_project_role_approval_reference_and_immutable_audit(tmp_path):
    governance = _governance(tmp_path)
    governance.add_member("alpha", "researcher-a", "researcher")
    governance.add_member("alpha", "operator-a", "operator")
    governance.add_member("alpha", "approver-a", "approver")
    audit = TamperEvidentAuditLog(tmp_path / "audit.sqlite")
    service = GovernedActionService(governance, audit)

    with pytest.raises(PermissionError, match="approval"):
        service.authorize(
            _principal("approver-a"),
            "alpha",
            action="release.activate",
            target_kind="release",
            target_id="release-001",
            reason="certified release",
        )
    with pytest.raises(PermissionError, match="not authorized"):
        service.authorize(
            _principal("researcher-a"),
            "alpha",
            action="release.activate",
            target_kind="release",
            target_id="release-001",
            reason="attempt",
            approval_id="approval-001",
        )
    decision = service.authorize(
        _principal("approver-a"),
        "alpha",
        action="release.activate",
        target_kind="release",
        target_id="release-001",
        reason="certified release",
        approval_id="approval-001",
    )
    operator = service.authorize(
        _principal("operator-a"),
        "alpha",
        action="executor.cancel",
        target_kind="run",
        target_id="run-001",
        reason="operator requested cancellation",
    )

    assert decision.approval_id == "approval-001"
    assert operator.action == "executor.cancel"
    events = audit.events()
    assert [event.outcome for event in events] == ["DENIED", "DENIED", "SUCCEEDED", "SUCCEEDED"]
    verification = audit.verify()
    assert verification.event_count == 4
    assert len(verification.head_hash) == 64


def test_entitlement_registry_blocks_cross_project_use_and_disallowed_export():
    registry = EntitlementRegistry()
    restricted = registry.register(
        DataEntitlement(
            entitlement_id="license-a",
            provider="vendor-a",
            license_id="contract-2026",
            allowed_projects=frozenset({"alpha"}),
            export_allowed=False,
        )
    )
    assert registry.require_use("alpha", restricted.entitlement_id) == restricted
    with pytest.raises(PermissionError, match="not entitled"):
        registry.require_use("beta", restricted.entitlement_id)
    with pytest.raises(PermissionError, match="forbids"):
        registry.require_export("alpha", restricted.entitlement_id)

    exportable = registry.register(replace(restricted, entitlement_id="license-b", export_allowed=True))
    assert registry.require_export("alpha", exportable.entitlement_id) == exportable
