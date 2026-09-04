# Research subsystem rules

Before modifying governed research behavior, identify the research phase, input and output artifact identities, state transition, and final-holdout implication. Read the governing documentation and configuration, not only the implementation.

## Package architecture

- Put canonical implementation under responsibility-oriented packages: `contracts/`, `evidence/`, `features/`, `hypotheses/`, `workflow/`, `evaluation/`, `diagnostics/`, `portfolio/`, or `reporting/`.
- Root-level `phaseN_*` modules are backward-compatibility shims only. They must not contain business logic, helper functions, classes, thresholds, or research policy.
- Shared deterministic artifact writing belongs in `artifact_io.py`; do not duplicate immutable JSON/checksum writers across programs.
- Test fixtures, synthetic evidence, failure injection, and architecture assertions belong under `tests/`, never in runtime research modules.

## Governance invariants

- Preserve point-in-time timing, label alignment, purge/embargo, per-fold fitted-state isolation, and non-overlapping ordered OOS stitching.
- Preserve deterministic artifact/caching/checkpoint identity and fail closed on identity, lineage, checksum, or temporal-semantics drift.
- Do not infer that weak research quality disproves the certified infrastructure baseline without contradictory invariant evidence.
- Temporal or selection-semantics changes require explicit leakage and holdout-isolation tests.
- The active Phase 3-D program permits only P3-D00 through P3-D04 diagnostic work. Do not create candidates, select/promote models, run P2-R01 through P2-R03, open the final holdout, or authorize publishing. Use the `research-diagnostics` Skill and its Phase 3-D profile for Phase 3 work.
