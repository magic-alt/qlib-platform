---
status: ACTIVE
owner: maintainers
applies_to_commit: 85bac85356d8092adfe98cd82ee59f81a242cf53
last_verified: 2026-09-02
---

# Repository Settings

This page records the intended public GitHub metadata and maintainer-facing repository settings for `magic-alt/qlib-platform`. It exists because some GitHub settings are not represented by tracked files and therefore cannot be code-reviewed in normal pull requests.

## About panel

Recommended repository **Description**:

> Auditable A-share quant research & alpha factory built on Microsoft Qlib — immutable data lineage, walk-forward evaluation, reproducible backtests and governed portfolio handoff.

Recommended **Website** after GitHub Pages is enabled:

```text
https://magic-alt.github.io/qlib-platform/
```

Recommended **Topics**:

```text
qlib
quantitative-finance
algorithmic-trading
a-share
alpha-research
machine-learning
backtesting
walk-forward
portfolio-optimization
tushare
fintech
research-platform
```

## Features

Recommended public settings:

| Setting | Recommendation | Rationale |
| --- | --- | --- |
| Issues | Enabled | Structured bug/feature forms are tracked in `.github/ISSUE_TEMPLATE/`. |
| Discussions | Optional / enable when community traffic justifies it | Avoid an empty support surface before there is recurring community usage. |
| Wiki | Disable once the MkDocs site is authoritative | Version-controlled docs are easier to review, verify, and keep aligned with code. |
| Projects | Optional | Useful only when the repository starts using public roadmap/project boards. |
| Releases | Use for versioned software releases | Release notes and changelog should remain aligned. |

## Pull requests

Recommended merge policy:

- prefer **Squash and merge** for normal feature/fix PRs;
- allow merge commits only for cases where preserving branch topology is materially useful;
- delete merged branches automatically;
- require the branch to be up to date when a change touches governed contracts or high-risk research semantics;
- require CODEOWNERS review once there is more than one active maintainer.

For a single-maintainer repository, CODEOWNERS still documents ownership intent even if branch protection cannot practically require a second independent reviewer.

## Branch protection / ruleset

Recommended `main` ruleset:

- require a pull request before merging;
- require status checks for governance and the relevant test/quality jobs;
- block force pushes and branch deletion;
- require conversation resolution;
- require linear history if the project standardizes on squash/rebase merges;
- do not allow routine bypasses for governed contract or research-semantic changes.

The exact required checks should match stable job names in `.github/workflows/ci.yml`; do not hard-code stale names here if CI is reorganized.

## GitHub Pages

The repository tracks an MkDocs Material configuration and a documentation workflow. To publish the site:

1. Open **Settings → Pages**.
2. Select **GitHub Actions** as the Pages source.
3. Add repository variable `DOCS_PAGES_ENABLED=true`.
4. Push a documentation change to `main` or manually run the Docs workflow.
5. After the first successful deployment, set the repository Website field to `https://magic-alt.github.io/qlib-platform/`.

The docs workflow intentionally skips deployment until the repository variable is enabled, so introducing the workflow cannot break CI merely because Pages has not yet been configured.

## Licensing

The repository is licensed under Apache License 2.0. Keep the root `LICENSE` file intact. Third-party components, datasets, examples, generated assets, or copied code may have separate attribution/license requirements; document those requirements rather than assuming the repository license can overwrite them.
