---
status: ACTIVE
owner: architecture
applies_to_commit: 0b88ee912d5a5ef9135b5113a32d886e9da1e0a6
last_verified: 2026-09-06
---

# Current State

This page is the single source of truth for fast-changing governance state. Frozen certification/history documents must not copy a moving “current main” SHA into their own normative text.

| Field | Current governed value |
| --- | --- |
| Documentation audit base | `0b88ee912d5a5ef9135b5113a32d886e9da1e0a6` (2026-09-06); P5-A merged baseline |
| Reviewed code baseline | `0b88ee912d5a5ef9135b5113a32d886e9da1e0a6` |
| Reviewed baseline date | 2026-09-06 |
| Certified infrastructure baseline | `4f5c5d5` |
| Certification date | 2026-08-17 |
| P0–P4 repository revalidation | `a74e568b0f1660da9bbbc6ed8ff6203c001f1e58`; see [P0–P4 Repository Revalidation](p0_p4_repository_recertification.md) |
| P5-A acceptance | `COMPLETE / MERGED`; PR #99, merge `0b88ee912d5a5ef9135b5113a32d886e9da1e0a6` |
| Active infrastructure program | P5-B / Institutional Portfolio Construction |
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

- **Documentation audit base** identifies the repository tree against which active documentation and the current infrastructure program are checked.
- **Reviewed code baseline** is the latest explicitly reviewed and merged repository baseline recorded by the governance process.
- **Certified infrastructure baseline** remains the frozen commit covered by the 2026-08-17 full research-infrastructure certificate.
- **P0–P4 repository revalidation** is the narrower repository-level revalidation completed before P5 started.
- **P5-A acceptance** records the risk-platform workstream that was validated through its dedicated contract and full repository CI before merge.

These values deliberately distinguish historical full acceptance, repository revalidation and later additive P5 workstreams.

## Certification interpretation

`Research Infrastructure: CERTIFIED` applies to the frozen code baseline `4f5c5d5` and the scope defined by [Research Infrastructure Certification](research_infrastructure_certification.md).

`P0_P4_REPOSITORY_REVALIDATED` applies to `a74e568b0f1660da9bbbc6ed8ff6203c001f1e58` and the narrower scope defined by [P0–P4 Repository Revalidation](p0_p4_repository_recertification.md). It records successful repository/compatibility/security/cross-platform revalidation and does not pretend that the historical full-walk-forward acceptance campaign was rerun.

P5-A was subsequently merged at `0b88ee912d5a5ef9135b5113a32d886e9da1e0a6` after its dedicated risk contract and full repository CI completed successfully. P5-B starts from that merged baseline and must establish its own deterministic portfolio-construction contract before it is complete.

Weak research results are still attributed first to alpha, regime, model or portfolio research when no certified invariant produces contradictory evidence. This attribution policy is not a substitute for revalidating material behavioral changes.

## Active research restrictions

Phase 3-D remains diagnosis-only:

- `formalCandidatesAllowed=false`;
- `publishingAuthorized=false`;
- `finalHoldout.accessAllowed=false`;
- no P2-R01 through P2-R03;
- no candidate creation, model selection or automatic confirmatory hypothesis.

The P5 infrastructure program does not alter those restrictions. P5-B may add benchmark-relative optimization, portfolio risk budgets, transaction-cost constraints and implementation transforms, but it must not consume the final holdout or change research-selection state.

P5-B also does not move broker/OMS authority into this repository. Continuous target weights and A-share round-lot research outputs remain research-side portfolio construction; authoritative order state and live hard-risk enforcement remain execution-platform responsibilities.

Do not run `stability-diagnose` merely to validate documentation. It writes an immutable evidence directory and requires explicit authorization of the exact output.

The presence of generic model/refit/export CLI commands does not override these active-program restrictions.

## Update policy

Update this page whenever the active infrastructure or research program, reviewed code baseline, certified baseline, default profile, holdout state, publishing state, artifact contract or documentation-audit base changes. Do not copy these moving facts into frozen/historical research protocols.
