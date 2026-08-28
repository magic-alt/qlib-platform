---
status: ACTIVE
owner: architecture
applies_to_commit: 8692afefe1f6cc82ab1f276fca788888f9f30f3e
last_verified: 2026-08-28
---

# Current State

| Field | Current governed value |
| --- | --- |
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

## Certification interpretation

`Research Infrastructure: CERTIFIED` applies to the frozen code baseline `4f5c5d5` and the scope
defined by [Research Infrastructure Certification](research_infrastructure_certification.md).
It does not mean that every post-baseline change on current main has automatically inherited the same
claim. Material changes require targeted incremental revalidation or a new certification record.

Weak research results are still attributed first to alpha, regime, model or portfolio research when no
certified invariant produces contradictory evidence. This attribution policy is not a substitute for
revalidating post-baseline code changes.

## Active research restrictions

Phase 3-D remains diagnosis-only:

- `formalCandidatesAllowed=false`;
- `publishingAuthorized=false`;
- `finalHoldout.accessAllowed=false`;
- no P2-R01 through P2-R03;
- no candidate creation, model selection or automatic confirmatory hypothesis.

Do not run `phase3-diagnose` merely to validate documentation. It writes an immutable evidence directory
and requires explicit authorization of the exact output.

## Update policy

Update this page whenever the active program, reviewed code baseline, certified baseline, default profile,
holdout state, publishing state or artifact contract changes. Do not copy these facts into historical
research documents.
