---
status: FROZEN
owner: architecture
applies_to_commit: a74e568b0f1660da9bbbc6ed8ff6203c001f1e58
last_verified: 2026-09-06
---

# P0–P4 Repository Revalidation

## Decision

The merged P0–P4 repository baseline at commit
`a74e568b0f1660da9bbbc6ed8ff6203c001f1e58` is:

> **P0–P4 repository baseline: REVALIDATED**

This record closes the post-baseline repository revalidation required before starting the
institutional P5 program. It does **not** replace the frozen 2026-08-17 research-infrastructure
certificate and it does not authorize a research-phase transition.

## Scope

The revalidation covers the repository state after P4 — Qlib Native Compatibility & Superset
Contract was merged to `main`, including:

- the existing P0–P3 data, research-governance, evidence, portfolio, artifact and execution-boundary
  invariants already protected by repository tests;
- P4 native Qlib passthrough, capability manifest, qrun/task_train/Recorder bridge, generic
  Dataset/Handler/Processor/Strategy/Executor construction and Recorder federation;
- secure packaging behavior for the pinned Qlib substrate and the documented fail-closed upstream
  RL dependency exception;
- wheel/install isolation, standalone behavior, model-bundle parity and LightGBM clean-machine
  execution;
- Linux, Windows and macOS repository compatibility represented by the project CI matrix;
- lint, formatter, static typing, coverage, governance/project-audit, documentation and CodeQL gates.

## Evidence

The merge commit was produced by PR #98 and the post-merge `main` CI run
`33977491440` completed successfully. The corresponding post-merge Windows 3.12 matrix check also
completed successfully. P4's dedicated Qlib Capability Contract passed on the reviewed PR head
before merge, together with the repository quality, security, documentation, release and macOS
checks.

The P4 corrective commits also closed the two issues discovered by CI during implementation:

1. Ruff formatter drift in the new compatibility tests;
2. the `importlib.resources.abc.Traversable.joinpath` static-typing mismatch in capability-manifest
   resolution.

No CI gate was disabled or weakened to reach this decision.

## What this record does not claim

This is a **repository-level revalidation**, not a rerun of the historical full-walk-forward
acceptance campaign. Therefore:

- the frozen Research Infrastructure Certification remains anchored at `4f5c5d5`;
- this record does not claim that a particular alpha/model/portfolio has research quality;
- this record does not create or promote a formal research candidate;
- the final holdout remains sealed;
- Phase 3-D diagnosis-only restrictions remain in force;
- publishing remains unauthorized;
- broker/OMS/order-state responsibilities remain outside this repository.

## P5 entry condition

P5 may add institutional research infrastructure on top of this revalidated repository baseline
provided each P5 workstream remains additive, preserves the governed research and execution
boundaries, and receives its own tests and CI evidence.

The agreed sequence is:

1. **P5-A Risk Platform**;
2. **P5-B Portfolio Construction**;
3. **P5-C Execution Research**;
4. **P5-D Enterprise Research Management**.

P5 infrastructure work does not itself change the active research program or unlock the sealed
holdout.
