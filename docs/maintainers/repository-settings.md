---
status: ACTIVE
owner: maintainers
applies_to_commit: 08f4d40397a7c0a215428ccdbdc4597865cfa5fe
last_verified: 2026-09-02
---

# Repository Settings

This page records public GitHub metadata and settings that live outside tracked files. Security, Ruleset, dependency, and supply-chain policy is defined in [Repository Governance](repository-governance.md).

## About panel

Recommended **Description**:

> Auditable A-share quant research & alpha factory built on Microsoft Qlib — immutable data lineage, walk-forward evaluation, reproducible backtests and governed portfolio handoff.

Recommended **Website** after Pages deployment:

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

## Repository features

| Setting | Recommendation |
| --- | --- |
| Issues | Enabled; use structured forms and contributor-task proposal form |
| Discussions | Enable when recurring community support/design traffic justifies it |
| Wiki | Disable once MkDocs Pages is authoritative |
| Projects | Optional; use when public roadmap tracking needs a board |
| Releases | Enabled for automated versioned software releases |
| Sponsorship | Optional; only if a real project funding channel exists |

## Pull-request settings

Recommended:

- enable **Squash merge** as the normal merge mode;
- optionally keep Rebase merge if maintainers need it;
- disable routine merge commits if linear history is enforced;
- automatically delete head branches after merge;
- enable auto-merge only when required checks/rulesets make it safe;
- do not require CODEOWNER approval until a second trusted maintainer can provide independent review.

The authoritative `main`/tag Ruleset recommendations and required checks are in [Repository Governance](repository-governance.md).

## GitHub Pages

The repository tracks MkDocs Material plus a strict build/deploy workflow. To make the site live after the documentation PR is merged:

1. Open **Settings → Pages**.
2. Select **GitHub Actions** as the source.
3. Add repository variable `DOCS_PAGES_ENABLED=true`.
4. Run **Docs** manually or merge/push a documentation change to `main`.
5. Confirm deployment to `https://magic-alt.github.io/qlib-platform/`.
6. Set the About **Website** field to that URL.

PRs build with `mkdocs build --strict` but do not deploy.

## Security & analysis prerequisites

Under **Settings → Security & analysis**:

- enable **Dependency graph** before making Dependency Review a required check;
- enable Dependabot alerts/security updates where available;
- keep CodeQL/default code scanning results visible to maintainers.

The repository intentionally keeps Dependency Review fail-closed when the dependency graph is unavailable rather than silently downgrading the check.

## Milestones

Create GitHub milestone objects corresponding to the tracked [Project Roadmap](../project/roadmap.md):

- `M0 — Open-source foundation`
- `M1 — Contributor-ready research platform`
- `M2 — Reproducible software releases`
- `M3 — Stable research interfaces`
- `M4 — v1.0 readiness`

Milestone descriptions should link back to the roadmap rather than duplicating detailed exit criteria in the GitHub UI.

## Labels

Ensure these public contribution labels exist and are used deliberately:

- `good first issue`
- `help wanted`
- `documentation`
- `research`
- `data`
- `contracts`
- `operations`
- `security`

The [Good First Issue Policy](../project/good-first-issues.md) defines when newcomer labels are appropriate.

## Licensing

The repository is Apache-2.0 licensed. Third-party code, datasets, examples, and assets retain their own attribution and license obligations; do not assume the repository license overrides them.
