---
status: ACTIVE
owner: maintainers
last_verified: 2026-09-02
---

# Release Process

This page defines the maintainer workflow for versioned `qlib-platform` software releases. It does **not** authorize research promotion, final-holdout access, model publishing, broker execution, or any other research lifecycle transition.

## Versioning

Use `MAJOR.MINOR.PATCH`.

- **MAJOR** — incompatible public API, artifact-contract, identity/lineage, persisted-schema, or operational-contract changes requiring coordinated migration.
- **MINOR** — backward-compatible features, commands, model/research capabilities, artifact types, or materially expanded workflows.
- **PATCH** — backward-compatible fixes, correctness hardening, documentation/packaging fixes, and operational repairs.

A source-compatible change may still deserve a larger bump when it changes governed semantics such as label definitions, causal timing, portfolio-policy decisions, or artifact identity.

## Release architecture

The release process separates **review** from **publication**:

```text
release preparation PR
  -> required CI/security/docs checks
  -> merge to main
  -> immutable vX.Y.Z tag
  -> Release workflow
  -> wheel + sdist
  -> clean-wheel smoke test
  -> CycloneDX SBOM + SHA256SUMS
  -> provenance + SBOM attestations
  -> GitHub Release
```

The tag is the explicit publication trigger. The release workflow must not create a release from an arbitrary branch or a version that differs from `pyproject.toml`.

## Prepare the release PR

Before tagging:

1. Ensure `main` and the release PR are green under required CI, docs, security, and dependency checks.
2. Confirm [Current Governance State](../current_state.md) is accurate; do not rewrite frozen evidence to match current code.
3. Move relevant entries from `CHANGELOG.md` **Unreleased** into a dated version section.
4. Set the same version in `pyproject.toml`.
5. Review API/schema/identity/contract migration implications.
6. Confirm user-facing docs and examples match supported behavior.
7. Verify that no credentials, account identifiers, local paths, private datasets, or generated research evidence are accidentally included.

Higher-risk release changes include:

- `DataRelease` / `DatasetVersion` identity or verification;
- feature, label, PIT timing, or fold semantics;
- model bundle / prediction snapshot identity;
- portfolio-policy or research-backtest accounting;
- Artifact Contract schemas or handoff;
- outbox/acknowledgement semantics;
- production-refit/live-inference behavior;
- governance gates, holdout access, or promotion rules.

Those require targeted validation in addition to the ordinary package pipeline.

## Create the release tag

After the release PR is merged and `main` is green:

```bash
git switch main
git pull --ff-only
git tag -s vX.Y.Z -m "qlib-platform vX.Y.Z"
git push origin vX.Y.Z
```

If signed tags are not configured, use an annotated tag rather than a lightweight tag.

Never create the tag before the version/changelog PR has merged. The workflow verifies both:

- `vX.Y.Z` matches `project.version` in `pyproject.toml`;
- the tagged commit is reachable from `main`.

A `v*` tag ruleset should prevent deletion or movement of published tags. See [Repository Governance](repository-governance.md).

## Automated artifacts

`.github/workflows/release.yml` performs publication. It builds and uploads:

- Python wheel;
- source distribution;
- `sbom.cdx.json` — CycloneDX dependency inventory from a clean wheel environment;
- `SHA256SUMS` — checksums for package and SBOM artifacts.

It also creates GitHub artifact attestations:

- **build provenance** for the release artifacts;
- **SBOM attestation** binding the wheel to `sbom.cdx.json`.

The workflow then creates the GitHub Release using generated release notes. `.github/release.yml` controls note categories; `CHANGELOG.md` remains the curated user-visible version history.

PyPI publication is intentionally not enabled yet. If added later, use PyPI Trusted Publishing rather than a long-lived API token.

## Verify a release

A consumer or maintainer should be able to verify downloaded artifacts independently:

```bash
sha256sum -c SHA256SUMS
gh attestation verify <artifact> --repo magic-alt/qlib-platform
```

A clean wheel install should also succeed without a source checkout.

## Release notes

Release notes should explain:

- user/integrator-visible changes;
- breaking API/schema/identity/contract changes;
- required migrations;
- material research-semantic changes;
- important fixes or security changes;
- known limitations.

Do not describe a software release as successful alpha/model certification unless an independent governed research process explicitly supports that claim.

## Failure and rollback

Do not move or overwrite a published tag or silently replace release assets.

If a release is defective:

1. mark the release as affected/deprecated when appropriate;
2. fix forward in a new patch release;
3. document the defect and remediation in `CHANGELOG.md`;
4. create new immutable research evidence/identities if the defect affects governed outputs rather than mutating historical artifacts.

If the release workflow fails before publication, fix the release automation or release-preparation commit, merge the correction, increment/recreate an appropriate not-yet-published tag only if no public immutable tag/release has been established. Once a tag is public and governed as immutable, prefer a new version.
