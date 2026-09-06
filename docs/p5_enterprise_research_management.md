---
status: ACTIVE
owner: research-platform
applies_to_commit: 34dc33f6a2a56d87f9e67a2291809a4c52d0c158
last_verified: 2026-09-06
---

# P5-D — Enterprise Research Management

P5-D adds enterprise identity, ownership, authorization and audit controls around the research
platform without changing the research-program authorization state. It starts from the merged P5-C
baseline `34dc33f6a2a56d87f9e67a2291809a4c52d0c158`.

## Responsibility boundary

P5-D is a governance layer around existing research capabilities. It does not replace
`ExperimentStore`, research artifacts, release lineage, model evaluation, portfolio construction or
execution research.

It also does not grant any of the following:

- model-selection or formal-candidate authority;
- model-promotion approval;
- access to the sealed final holdout;
- publishing authorization;
- broker/OMS order authority;
- live hard-risk authority.

Authentication and research-resource authorization are intentionally separate from those controls.
The active research program remains Phase 3-D diagnosis-only.

## Federated identity integration

`qlib_platform.auth.federated` defines deployment-neutral identity-mapping contracts for:

- OIDC/OAuth2/SSO claims that have already passed signature/JWK verification at the deployment
  trust boundary;
- normalized LDAP/Active Directory directory records;
- group-to-platform-role mapping;
- issuer, audience, expiration, not-before, subject and username validation.

The mapper deliberately accepts **verified claims**, not a raw JWT. A deployment must validate the
ID-token signature, algorithm and key chain before passing claims into qlib-platform.

## Project ownership and resource-scoped RBAC

`ResearchGovernanceStore` persists:

- research projects and stable owners;
- project membership (`owner`, `maintainer`, `researcher`, `viewer`);
- project binding for experiments, datasets and artifacts;
- explicit subject/resource grants.

`ResearchAccessPolicy` is deny-by-default. Project roles grant only research-management permissions
(`read`, `write`, `run`, `manage`, `share`). Promotion, execution, broker and OMS namespaces are
explicitly outside the policy; even a global platform `admin` cannot acquire those authorities from
this policy.

## Service accounts and API tokens

`ServiceTokenStore` provides lifecycle contracts for high-entropy automation credentials:

- service-account creation/disable;
- scoped token issue;
- expiry;
- verification;
- revocation;
- rotation;
- hash-only secret storage.

Allowed scopes are restricted to research/project/dataset/artifact operations. Promotion and
execution scopes fail closed. A plaintext token is returned only at issue/rotation time and is never
stored by the token database.

## Tamper-evident audit events

`TamperEvidentAuditLog` exposes an append-only API backed by a SHA-256 event chain. Every event binds
its sequence, actor, action, governed resource, outcome, metadata and previous hash.

`verify()` detects mutation and reordering. The returned chain head may be anchored in external
immutable/WORM storage; passing that external head back to `verify()` also detects tail truncation.
Sensitive metadata fields such as passwords, credentials, authorization headers, secrets and tokens
are rejected rather than logged.

The hash chain is tamper-evident evidence, not a claim that a local SQLite file is physically
immutable against an operating-system administrator.

## Governed ExperimentStore façade

`GovernedExperimentStore` delegates storage and queries to the existing `ExperimentStore`. It adds:

1. project/resource authorization before access;
2. project binding for newly registered experiments/artifacts;
3. success/denial audit events;
4. fail-closed behavior for legacy resources that are not bound to a governed project.

This preserves one experiment/evidence persistence implementation instead of creating a parallel
enterprise registry.

## Automated certification

`scripts/run_comprehensive_checks.py` is the portable repository certification entry point. By
default it executes:

1. Ruff lint;
2. Ruff formatting verification;
3. mypy over `src`;
4. documentation governance contract;
5. qrun contract validation;
6. project audit;
7. the complete pytest suite with repository-wide coverage.

The default repository-wide coverage gate is **85%**. The runner emits a machine-readable JSON
report and returns non-zero if any contract fails. `--skip-governance` exists only for focused local
iteration; it is not the certification mode.

The dedicated `P5 Enterprise Research Management Contract` additionally runs P5-D tests together
with P5-A, P5-B and P5-C regression contracts and enforces a dedicated unit-coverage floor for the
P5-D authorization/management surface.

## Gate to completion

P5-D is complete only when one immutable PR head passes:

- P5-A risk contract;
- P5-B portfolio contract;
- P5-C execution contract;
- P5-D enterprise-management contract;
- repository-wide 85% coverage and full pytest suite;
- Linux/Windows/macOS compatibility gates;
- Qlib capability contract;
- docs, release, dependency-review and CodeQL gates.
