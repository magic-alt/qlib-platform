# Research configuration rules

Research configuration is a governed definition, not a free-form tuning surface. Before changing a value, identify the research phase, artifact identity impact, expected state transition, and required validation.

Do not silently change frozen anchors, comparisons, labels, split semantics, holdout state, regime definitions, thresholds, or experiment identity to improve a result or make a test pass. Prefer a new versioned configuration and explicit lineage when an authorized research change genuinely requires a new definition.

For `ashare_phase3_v1.yaml`, preserve the three Phase 2 anchors, causal regime specification, diagnostic windows, `formalCandidatesAllowed: false`, `publishingAuthorized: false`, and `finalHoldout.accessAllowed: false`. Use the `phase3-diagnostics` Skill before modifying or interpreting Phase 3 configuration.
