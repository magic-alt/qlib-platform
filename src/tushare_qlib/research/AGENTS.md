# Research subsystem rules

Before modifying governed research behavior, identify the research phase, input and output artifact identities, state transition, and final-holdout implication. Read the governing documentation and configuration, not only the implementation.

- Preserve point-in-time timing, label alignment, purge/embargo, per-fold fitted-state isolation, and non-overlapping ordered OOS stitching.
- Preserve deterministic artifact/caching/checkpoint identity and fail closed on identity, lineage, checksum, or temporal-semantics drift.
- Do not infer that weak research quality disproves the certified infrastructure baseline without contradictory invariant evidence.
- Temporal or selection-semantics changes require explicit leakage and holdout-isolation tests.
- The active Phase 3-D program permits only P3-D00 through P3-D04 diagnostic work. Do not create candidates, select/promote models, run P2-R01 through P2-R03, open the final holdout, or authorize publishing. Use the `phase3-diagnostics` Skill for Phase 3 work.
