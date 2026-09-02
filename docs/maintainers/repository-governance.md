---
status: ACTIVE
owner: maintainers
applies_to_commit: 08f4d40397a7c0a215428ccdbdc4597865cfa5fe
last_verified: 2026-09-02
---

# Repository Governance

This document defines the intended GitHub governance and software supply-chain posture for `qlib-platform`.

It covers repository settings that are partly stored outside Git, plus tracked automation such as Dependabot, CodeQL, Dependency Review, release validation/provenance, and Pages deployment.

## Current ruleset audit

As of 2026-09-02, the repository exposes one active ruleset named `develop`. The visible rule payload contains only:

- branch deletion protection;
- non-fast-forward protection.

That is useful but insufficient as the primary `main` merge policy for a mature public project.

The connected GitHub integration can read public rulesets but does not expose a ruleset/branch-protection mutation endpoint. Therefore the settings below must be applied in GitHub Settings by a repository administrator.

## Recommended `main` ruleset

Create or replace with a ruleset named **`main-protection`** targeting the default branch.

Recommended bootstrap settings for a single-maintainer repository:

| Rule | Setting |
| --- | --- |
| Enforcement | Active |
| Target | Default branch (`main`) |
| Restrict deletions | Enabled |
| Block force pushes / non-fast-forward | Enabled |
| Require pull request before merging | Enabled |
| Required approvals | **0 while there is only one maintainer** |
| Require conversation resolution | Enabled |
| Require status checks | Enabled |
| Require branches to be up to date | Enabled |
| Require linear history | Enabled |
| Require signed commits | Optional until local signing is consistently configured |
| Bypass | No routine bypass; emergency admin bypass only when explicitly documented |

Do **not** require a CODEOWNER approval while the repository has only one active CODEOWNER. GitHub does not allow the author to satisfy a self-review requirement, which would make ordinary maintenance impossible. Raise required approvals to 1 and require CODEOWNER review when a second trusted maintainer is active.

### Required checks

Use stable check names and update the ruleset whenever workflow names change. Recommended required checks after this PR lands:

- `CI / governance`
- `CI / quality`
- `CI / test-matrix (ubuntu-latest, 3.12)`
- `CI / test-matrix (windows-latest, 3.12)`
- `Docs / build`
- `Release Check / package-sbom`
- `Dependency Review / dependency-review`
- `CodeQL / Analyze (python)`

If GitHub renders a check name differently, select the exact name from a successful `main`/PR run rather than typing an assumed value.

## Tag protection

For release tags matching `v*`, use a separate active ruleset when possible:

- block deletion;
- block updates/non-fast-forward changes;
- restrict tag creation to maintainers/release automation;
- never move an existing public release tag to a different commit.

A bad release is superseded by a new version; published tags and artifacts are not silently rewritten.

## Dependency automation

The repository uses **Dependabot** as the canonical dependency bot.

Why not run Renovate simultaneously:

- both tools would open overlapping PRs;
- duplicated update streams create review noise and ambiguous ownership;
- a single dependency policy is easier to audit.

`.github/dependabot.yml` covers Python dependencies and GitHub Actions with weekly grouped minor/patch updates.

`pyqlib` and LightGBM are deliberately excluded from unattended version changes because they are governed compatibility-sensitive dependencies. Upgrade them in a dedicated PR with Qlib/backtest compatibility evidence.

Dependency PRs do not receive a reduced merge standard. They must pass the same required checks.

## Dependency Review

`.github/workflows/dependency-review.yml` runs on pull requests and fails on newly introduced dependencies with a **high or critical** known vulnerability.

The GitHub Dependency graph must be enabled under **Settings → Security & analysis** before this workflow can pass. Keep the workflow fail-closed rather than suppressing this prerequisite.

## CodeQL

`.github/workflows/codeql.yml` runs Python CodeQL analysis on pull requests to `main`, pushes to `main`, and a scheduled weekly scan.

CodeQL alerts are security findings, not automatic proof of exploitability. Triage findings in context, but do not silence alerts merely to make the workflow green.

## Release validation and supply chain

Release packaging is checked in two stages.

### Pull-request release check

`.github/workflows/release-check.yml` runs when packaging/source/release-workflow inputs change. It is read-only and verifies before merge that the repository can:

1. build wheel and source distribution;
2. install the built wheel in a clean environment;
3. import the package and resolve package metadata;
4. generate a reproducible CycloneDX JSON SBOM from the clean wheel environment;
5. generate and verify `SHA256SUMS`.

This prevents the first `vX.Y.Z` tag from being the first real execution of the packaging/SBOM path.

### Tagged release

`.github/workflows/release.yml` creates a GitHub software release only from an explicit `vX.Y.Z` tag. It additionally:

1. verifies the tag version matches `pyproject.toml`;
2. verifies the tagged commit is on `main`;
3. rebuilds and smoke-tests the distribution;
4. generates the CycloneDX SBOM and `SHA256SUMS`;
5. creates SLSA-style GitHub build-provenance attestations with `actions/attest`;
6. creates an SBOM attestation for the wheel;
7. publishes the artifacts to a GitHub Release.

This is a **software release**. It does not certify a strategy, change research promotion state, or authorize production trading.

Verification example after a release:

```bash
gh attestation verify <artifact> --repo magic-alt/qlib-platform
sha256sum -c SHA256SUMS
```

PyPI publishing is intentionally not part of this workflow yet. If added later, use PyPI Trusted Publishing rather than a long-lived API token.

## SBOM policy

The release SBOM is generated from a clean virtual environment containing the built wheel and its resolved runtime dependencies.

The SBOM is CycloneDX JSON, generated reproducibly where supported, attached to the GitHub Release, covered by the release checksum manifest, and used as the predicate for the wheel's SBOM attestation.

An SBOM is inventory evidence, not a vulnerability assessment.

## GitHub Pages

The docs workflow builds with:

```bash
python -m mkdocs build --strict
```

PRs only build; they do not deploy.

After this PR is merged, an administrator must configure **Settings → Pages → Source: GitHub Actions** and set `DOCS_PAGES_ENABLED=true`. The expected site is:

`https://magic-alt.github.io/qlib-platform/`

Once Pages is live, set the repository About website to that URL.

## Security permissions

Workflow permissions should be least-privilege:

- ordinary CI/docs/release check: `contents: read`;
- Dependency Review: `contents: read`;
- CodeQL: `contents: read`, `security-events: write`;
- Pages deploy: `pages: write`, `id-token: write`;
- tagged release: `contents: write`, `id-token: write`, `attestations: write`, `artifact-metadata: write`.

Third-party actions should be avoided when a GitHub-native or simple CLI implementation is sufficient. Actions used in this repository should be pinned to immutable commit SHAs and maintained by Dependabot.

## Milestones and issue labels

Use [Project Roadmap](../project/roadmap.md) for milestone intent and [Good First Issue Policy](../project/good-first-issues.md) for newcomer tasks.

GitHub milestone objects and labels are coordination metadata; they do not alter research governance or release authorization.

## Periodic maintainer audit

At least quarterly, or after a major GitHub Actions/security change:

- review rulesets and bypass actors;
- confirm required checks still map to real workflow check names;
- review CODEOWNERS;
- inspect Dependabot backlog and ignored dependencies;
- review CodeQL/security alerts;
- verify release workflow permissions and action SHAs;
- run a release check and docs strict build;
- verify Pages and repository About metadata;
- prune stale `good first issue` / `help wanted` tasks.
