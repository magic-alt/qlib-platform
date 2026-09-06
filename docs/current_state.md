---
status: ACTIVE
owner: architecture
applies_to_commit: a74e568b0f1660da9bbbc6ed8ff6203c001f1e58
last_verified: 2026-09-06
---

# Current State

This page is the single source of truth for fast-changing governance state. Frozen certification/history documents must not copy a moving “current main” SHA into their own normative text.

| Field | Current governed value |
| --- | --- |
| Documentation audit base | `a74e568b0f1660da9bbbc6ed8ff6203c001f1e58` (2026-09-06); P0–P4 repository revalidation base |
| Reviewed code baseline | `a74e568b0f1660da9bbbc6ed8ff6203c001f1e58` |
| Reviewed baseline date | 2026-09-06 |
| Certified infrastructure baseline | `4f5c5d5` |
| Certification date | 2026-08-17 |
| Post-baseline certification | `P0_P4_REPOSITORY_REVALIDATED`; see [P0–P4 Repository Revalidation](p0_p4_repository_recertification.md) |
| Active infrastructure program | P5-A / Institutional Risk Platform |
| Active research program | Phase 3-D / `ashare_alpha_stability_phase3_v1` |
| Permitted Phase 3 scope | P3-D00 through P3-D04 diagnostics |
| Formal candidates | Disallowed |
| Model selection | Disallowed |
| Final holdout | `SEALED`; access disallowed |
| Publishing in Phase 3-D | Disabled |
| CLI default config | `configs/pipeline.standalone.yaml` |
| Integrated profile | `configs/pipeline.integrated.yaml` |
| Artifact contract | v2 |
| Maximum qlib promotion state | `RESEARCH_PROMOTED` |
| Cross-repository execution handoff | One DataRelease-bound `TARGET_PORTFOLIO` |

## How to read the baselines

- **Documentation audit base** identifies the repository tree against which the active documentation and P0–P4 revalidation were checked.
- **Reviewed code baseline** is the latest explicitly reviewed repository baseline recorded by the governance process.
- **Certified infrastructure baseline** remains the frozen commit covered by the 2026-08-17 full research-infrastructure certificate.
- **Post-baseline certification** records the narrower repository-level revalidation performed after subsequent material engineering work.

These values deliberately distinguish historical full acceptance from current repository revalidation.

## Certification interpretation

`Research Infrastructure: CERTIFIED` applies to the frozen code baseline `4f5c5d5` and the scope defined by [Research Infrastructure Certification](research_infrastructure_certification.md).

`P0_P4_REPOSITORY_REVALIDATED` applies to `a74e568b0f1660da9bbbc6ed8ff6203c001f1e58` and the narrower scope defined by [P0–P4 Repository Revalidation](p0_p4_repository_recertification.md). It records successful repository/compatibility/security/cross-platform revalidation and does not pretend that the historical full-walk-forward acceptance campaign was rerun.

P5 infrastructure work starts from this revalidated P0–P4 repository baseline. Each P5 workstream must add its own deterministic tests and CI evidence and must not inherit an unsupported production or research-quality claim.

Weak research results are still attributed first to alpha, regime, model or portfolio research when no certified invariant produces contradictory evidence. This attribution policy is not a substitute for revalidating material behavioral changes.

## Active research restrictions

Phase 3-D remains diagnosis-only:

- `formalCandidatesAllowed=false`;
- `publishingAuthorized=false`;
- `finalHoldout.accessAllowed=false`;
- no P2-R01 through P2-R03;
- no candidate creation, model selection or automatic confirmatory hypothesis.

The P5 infrastructure program does not alter those restrictions. P5-A may add deterministic risk measurement, decomposition and stress-analysis infrastructure, but it must not consume the final holdout or change research-selection state.

Do not run `stability-diagnose` merely to validate documentation. It writes an immutable evidence directory and requires explicit authorization of the exact output.

The presence of generic model/refit/export CLI commands does not override these active-program restrictions.

## Update policy

Update this page whenever the active infrastructure or research program, reviewed code baseline, certified baseline, default profile, holdout state, publishing state, artifact contract or documentation-audit base changes. Do not copy these moving facts into frozen/historical research protocols.
