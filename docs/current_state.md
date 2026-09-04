---
status: ACTIVE
owner: architecture
applies_to_commit: 4f3f4369b6e55186967bc726bb8dd87fff0e5d70
last_verified: 2026-08-31
---

# Current State

This page is the single source of truth for fast-changing governance state. Frozen certification/history documents must not copy a moving “current main” SHA into their own normative text.

| Field | Current governed value |
| --- | --- |
| Documentation audit base | `4f3f4369b6e55186967bc726bb8dd87fff0e5d70` (2026-08-31); documentation verification only, not code certification |
| Reviewed code baseline | `8692afefe1f6cc82ab1f276fca788888f9f30f3e` |
| Reviewed baseline date | 2026-08-26 |
| Certified infrastructure baseline | `4f5c5d5` |
| Certification date | 2026-08-17 |
| Post-baseline certification | `INCREMENTAL_REVALIDATION_REQUIRED` |
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

## How to read the three baselines

- **Documentation audit base** tells you which current `main` tree the active documentation was checked against. It does not claim that code was revalidated.
- **Reviewed code baseline** is the latest explicitly reviewed code baseline recorded by the governance process.
- **Certified infrastructure baseline** is the frozen commit to which the infrastructure certification applies.

These values may legitimately differ.

## Certification interpretation

`Research Infrastructure: CERTIFIED` applies to the frozen code baseline `4f5c5d5` and the scope defined by [Research Infrastructure Certification](research_infrastructure_certification.md). It does not mean that post-baseline changes on current main inherit the same claim.

Material changes require targeted incremental revalidation or a new certification record. Documentation-only review cannot upgrade this status.

Weak research results are still attributed first to alpha, regime, model or portfolio research when no certified invariant produces contradictory evidence. This attribution policy is not a substitute for revalidating post-baseline code changes.

## Active research restrictions

Phase 3-D remains diagnosis-only:

- `formalCandidatesAllowed=false`;
- `publishingAuthorized=false`;
- `finalHoldout.accessAllowed=false`;
- no P2-R01 through P2-R03;
- no candidate creation, model selection or automatic confirmatory hypothesis.

Do not run `stability-diagnose` merely to validate documentation. It writes an immutable evidence directory and requires explicit authorization of the exact output.

The presence of generic model/refit/export CLI commands does not override these active-program restrictions.

## Update policy

Update this page whenever the active program, reviewed code baseline, certified baseline, default profile, holdout state, publishing state, artifact contract or documentation-audit base changes. Do not copy these moving facts into frozen/historical research protocols.
