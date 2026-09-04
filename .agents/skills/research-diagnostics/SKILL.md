---
name: research-diagnostics
description: Implement or audit governed research diagnostics, stability analysis, regime analysis, transition studies, decay analysis, and immutable diagnostic evidence without implicitly creating candidates, selecting models, opening holdout, or publishing.
---

# Governed research diagnostics

Use this skill when the requested outcome is diagnostic evidence about an existing research state, rather than a new alpha, model, candidate, selection decision, or publication.

Before acting, identify:

- the governing program, contract, configuration, and nearest `AGENTS.md`;
- the immutable input artifacts and their identities, lineage, checksums, and source revision;
- the diagnostic definitions, comparison groups, windows, regime specification, and permitted outputs;
- the allowed state transitions and whether final holdout, candidate creation, selection, retraining, or publishing is authorized.

Read only the program-specific documentation, configuration, implementation, and tests needed for the task. If the task concerns the active A-share stability-diagnostics program, also read [references/stability-diagnostics.md](references/stability-diagnostics.md) before interpreting, changing, running, or auditing it.

## Diagnostic boundary

Treat diagnostics as observation of an already governed research state. A diagnostic request does not by itself authorize retraining, feature or label changes, candidate registration, model or portfolio selection, promotion, final-holdout access, or publishing. Perform any such transition only when the governing program explicitly allows it and the user has authorized the state-changing action.

Preserve point-in-time causality, per-fold fitted-state isolation, ordered non-overlapping OOS stitching, immutable artifact identity, deterministic lineage and hashes, and final-holdout isolation. Never synthesize investable performance from forward labels or present descriptive slices as confirmatory evidence.

Frozen anchors, comparisons, windows, thresholds, and regime definitions are governed inputs, not tuning surfaces. Do not change them to improve a result or make validation pass. When a new definition is authorized, version it explicitly and preserve lineage to the prior definition.

Prefer diagnostics derived from existing immutable artifacts. If a diagnostic needs new fitted state or recomputation that could alter research identity, stop treating it as a read-only diagnostic and apply the governing research-change workflow and approvals as well.

## Evidence and verification

Bind every diagnostic result to its exact inputs, configuration, implementation/source revision, and diagnostic definition. Keep descriptive, exploratory, and confirmatory claims visibly distinct, and report negative or inconclusive findings without relaxing the certified infrastructure baseline.

Verification must fail closed when identity, timing, lineage, checksum, source revision, governed definitions, holdout state, or allowed research state cannot be proven. Include negative-path tests for any changed boundary or verifier behavior.

Running a diagnostic command or writing an evidence bundle is state-changing even when research state is not advanced. Obtain explicit user authorization, confirm the exact output location, and report the affected outputs before producing it.
