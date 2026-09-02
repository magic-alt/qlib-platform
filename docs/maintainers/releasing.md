---
status: ACTIVE
owner: maintainers
applies_to_commit: 85bac85356d8092adfe98cd82ee59f81a242cf53
last_verified: 2026-09-02
---

# Release Process

This page defines the maintainer workflow for versioned `qlib-platform` releases. It governs software/package releases; it does **not** authorize research promotion, final-holdout access, model publishing, broker execution, or any other research lifecycle transition.

## Versioning model

Use `MAJOR.MINOR.PATCH` version numbers.

- **MAJOR** — incompatible public API, artifact-contract, identity/lineage, persisted-schema, or operational-contract changes that require coordinated migration.
- **MINOR** — backward-compatible features, new commands, model/research capabilities, new artifact types, or materially expanded supported workflows.
- **PATCH** — backward-compatible bug fixes, correctness hardening, documentation fixes, packaging fixes, and operational repairs that do not introduce a new public contract.

A change can be technically source-compatible and still require a larger version bump when it changes governed research semantics. Examples include a new label definition, a changed portfolio-policy decision function, a new causal-timing rule, or a schema change that affects artifact identity.

## Release prerequisites

Before cutting a release:

1. Ensure `main` is green under required CI and governance checks.
2. Confirm [`Current State`](../current_state.md) accurately describes moving governance facts. Do not rewrite frozen certification history to match current `main`.
3. Update `CHANGELOG.md`: move relevant entries from **Unreleased** into a dated version section.
4. Update the package version in `pyproject.toml`.
5. Review contract/schema changes and migration requirements explicitly.
6. Confirm documentation examples still match the parser and supported configuration profiles.
7. Verify no credentials, account identifiers, workstation-local paths, private datasets, or generated research evidence are accidentally included.

## Release classification checklist

Treat a release as higher risk when it changes any of the following:

- `DataRelease` / `DatasetVersion` identity or verification;
- feature, label, PIT timing, or fold construction semantics;
- model-bundle or prediction-snapshot identity;
- portfolio-policy decisions or backtest accounting;
- Artifact Contract schemas or cross-repository handoff;
- durable outbox / acknowledgement semantics;
- production-refit or live-inference behavior;
- governance gates, holdout access, or promotion rules.

Those changes require targeted validation beyond a routine package build.

## Recommended tag and GitHub Release flow

After the release PR is merged and `main` is green:

```bash
git switch main
git pull --ff-only
git tag -s vX.Y.Z -m "qlib-platform vX.Y.Z"
git push origin vX.Y.Z
```

If signed tags are not configured, use an annotated tag rather than a lightweight tag.

Create a GitHub Release from `vX.Y.Z`, use GitHub generated release notes as a starting point, and reconcile them against `CHANGELOG.md`. `.github/release.yml` groups generated notes by research/data/backtest/contracts/operations/documentation areas.

## Release notes structure

Release notes should answer:

- What changed for users or integrators?
- Are there breaking API, schema, identity, or artifact-contract changes?
- Are migrations required?
- Which validation evidence supports the release?
- Does the release affect research semantics or only infrastructure?
- What remains intentionally unsupported?

Do not describe an infrastructure release as a successful alpha/model certification unless the relevant governed research process independently authorizes that claim.

## Package smoke test

For a normal release candidate, verify at minimum:

```bash
python -m build --wheel
python -m pytest
python -m tushare_qlib status
python -m tushare_qlib health dependencies
```

Use the repository's CI jobs as the canonical cross-platform validation surface. Clean-machine wheel tests are especially important because editable installs can hide packaging omissions.

## Rollback

Do not move or overwrite an existing published tag. If a release is defective:

1. mark the GitHub Release as affected/deprecated when appropriate;
2. fix forward with a new patch version;
3. document the defect and remediation in `CHANGELOG.md`;
4. if the defect affects immutable evidence or research identity, create new evidence/versions rather than mutating old artifacts in place.
