---
status: ACTIVE
owner: maintainers
applies_to_commit: 08f4d40397a7c0a215428ccdbdc4597865cfa5fe
last_verified: 2026-09-02
---

# Project Roadmap

This roadmap communicates engineering direction for `qlib-platform`. It is **not** research authorization, a release promise, or an investment-performance target.

Fast-changing research authorization remains in [Current Governance State](../current_state.md). Software versions and GitHub milestones coordinate engineering work; they do not certify a model or unseal a holdout.

## Roadmap principles

Work is prioritized when it improves one or more of:

1. **Causal correctness** — PIT semantics, fold isolation, and governed holdout handling.
2. **Reproducibility** — immutable identities, manifests, deterministic replay, and artifact provenance.
3. **Research usefulness** — better diagnostics, models, portfolio policies, and evaluation without weakening controls.
4. **Operational reliability** — fail-closed integrity, idempotent recovery, observability, and standalone operation.
5. **Contributor leverage** — clear interfaces, documentation, tests, and bounded contribution paths.
6. **Supply-chain integrity** — reviewed dependencies, secure workflows, SBOMs, provenance, and reproducible releases.

## Milestone model

GitHub milestones should mirror the stages below. A milestone is a planning container only; it does not override `docs/current_state.md`.

### M0 — Open-source foundation

**Goal:** make the repository understandable, auditable, and safe to contribute to.

Exit criteria:

- professional README and architecture overview;
- Apache-2.0 licensing;
- contribution, security, conduct, and CODEOWNERS policies;
- MkDocs Material site with strict CI;
- repository governance guidance;
- dependency/security/release automation;
- SBOM and SLSA-style GitHub artifact attestations;
- public roadmap and first-contributor policy.

Status: **in progress through PR #57**.

### M1 — Contributor-ready research platform

**Goal:** make independent external contribution practical without weakening research governance.

Candidate work:

- stabilize public extension points for data, features, models, diagnostics, and portfolio policy;
- expand contract and failure-mode examples;
- increase targeted regression coverage around PIT and lineage;
- publish a maintained contributor-oriented sample workflow;
- maintain a small backlog of genuine `good first issue` and `help wanted` tasks;
- improve benchmark fixtures that do not require proprietary credentials or data.

Exit criteria:

- a new contributor can build, test, and submit a bounded change from public documentation alone;
- first-contributor tasks have explicit acceptance tests and do not require private infrastructure;
- no public extension point depends on undocumented mutable state.

### M2 — Reproducible software releases

**Goal:** make every public software release independently inspectable.

Candidate work:

- tagged GitHub releases generated from a reviewed `main` commit;
- wheel + source distribution smoke-tested on clean environments;
- SHA-256 checksum manifest;
- CycloneDX SBOM;
- GitHub artifact provenance and SBOM attestations;
- release notes linked to `CHANGELOG.md`;
- release verification documented with `gh attestation verify`.

Exit criteria:

- a release consumer can verify origin, checksums, package metadata, and dependency inventory;
- release automation cannot bypass CI or create a release from an unreviewed branch.

### M3 — Stable research interfaces

**Goal:** move high-value research contracts toward a stable pre-1.0 public surface.

Candidate work:

- versioned extension interfaces and deprecation policy;
- stronger schema compatibility tests;
- reproducible reference datasets/fixtures for public CI;
- standardized benchmark/report bundles;
- clearer separation between exploratory, research-promoted, and execution-consumable artifacts.

Exit criteria:

- compatibility expectations are documented;
- contract changes have explicit migration paths;
- public examples exercise the same interfaces recommended to downstream users.

### M4 — v1.0 readiness

`v1.0.0` should be considered only when:

- core identity and artifact contracts have stable compatibility guarantees;
- installation and clean-machine smoke tests are routine;
- security scanning and dependency review are enforced;
- release provenance/SBOMs are routine;
- the documented Research Plane / Execution Plane boundary is reflected in implementation and tests;
- maintainers are prepared to support a documented deprecation policy.

`v1.0.0` would mean **software API/contract maturity**. It would not mean any trading strategy is certified profitable or production-approved.

## Issue and milestone hygiene

Milestone issues should be scoped to a concrete deliverable and have observable acceptance criteria.

Recommended labels:

- `good first issue` — bounded, low-risk, independently verifiable newcomer task;
- `help wanted` — maintainer-reviewed task where outside contribution is welcome;
- `research` — methodology/model/diagnostic work;
- `data` — data release, PIT, calendar, or materialization;
- `contracts` — schema/identity/artifact-contract work;
- `operations` — runtime, outbox, scheduler, recovery, observability;
- `security` — security or supply-chain hardening;
- `documentation` — documentation-only or documentation-dominant work.

Do not use a milestone or label as evidence that a research gate has passed.

## How priorities change

The roadmap is intentionally revisable. Maintainers may reorder work in response to:

- correctness or security findings;
- upstream Qlib/Python changes;
- reproducibility gaps;
- user/contributor feedback;
- operating experience;
- cross-repository contract changes with `magic-alt/platform`.

Material roadmap changes should be reviewable through pull requests rather than silently edited only in GitHub UI.
