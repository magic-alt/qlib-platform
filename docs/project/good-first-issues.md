---
status: ACTIVE
owner: maintainers
last_verified: 2026-09-02
---

# Good First Issue Policy

`good first issue` is a maintainer promise about scope, not a marketing label.

A task should receive this label only when a contributor can complete it without private data, broker access, sealed-holdout access, or undocumented institutional knowledge.

## Eligibility

A good first issue should satisfy all of these:

- **Bounded:** normally one subsystem and a small number of files.
- **Low governance risk:** it must not require changing research gates, holdout authorization, promotion semantics, broker state, or security policy.
- **Reproducible publicly:** the problem can be reproduced with public code, fixtures, or synthetic data.
- **Observable acceptance criteria:** a contributor can tell when the task is complete.
- **Documented entry point:** the relevant code/docs and expected validation commands are linked.
- **Reviewable:** maintainers can validate the change without recreating private infrastructure.

Appropriate examples include:

- documentation/link corrections;
- focused regression tests;
- clearer error messages that preserve fail-closed behavior;
- small CLI UX improvements with no semantic change;
- test fixtures or synthetic examples;
- type hints or refactors with explicit behavior-preservation tests;
- documentation-site accessibility and navigation improvements.

Usually **not** appropriate:

- changing PIT/as-of semantics;
- changing sealed-holdout logic;
- changing research gates or thresholds;
- modifying Artifact Contract compatibility;
- live broker/QMT behavior;
- security-sensitive fixes before disclosure is complete;
- large architecture migrations.

## Required issue structure

Maintainer-created first issues should contain:

1. **Context** — why the task matters.
2. **Scope** — exact files/subsystem expected to change.
3. **Non-goals** — what must not change.
4. **Acceptance criteria** — observable completion checklist.
5. **Validation** — exact commands or tests.
6. **Pointers** — relevant docs, modules, or existing tests.
7. **Difficulty note** — what knowledge is useful, without overstating difficulty.

## Contributor workflow

For a first contribution:

1. comment on the issue before starting substantial work;
2. keep the PR limited to the stated scope;
3. include or update tests when behavior changes;
4. run the issue's validation commands;
5. link the issue in the PR;
6. disclose if the task turns out to require a governance-sensitive change.

If the task expands materially, the maintainer should split or reclassify it rather than silently turning a newcomer task into a high-risk change.

## Maintainer lifecycle

A healthy first-issue backlog is small and current.

Maintainers should:

- keep roughly **3–8** genuine first issues rather than labeling a large stale backlog;
- close or relabel tasks that are no longer reproducible;
- avoid assigning multiple contributors to the same small task without agreement;
- answer scope questions promptly enough that contributors are not forced to guess;
- convert recurring newcomer friction into documentation improvements.

`help wanted` may be used for larger or more specialized tasks that are contributor-friendly but not suitable as a first issue.

## Candidate starter backlog

These are examples to turn into live issues when they match current code:

- add focused tests for documentation-link classification and error output;
- improve CLI error messages for `DataRelease` vs `DatasetVersion` confusion without changing semantics;
- add synthetic examples for a small portfolio-policy edge case;
- improve MkDocs accessibility/alt text and mobile table behavior;
- add regression coverage for malformed manifest parent references;
- document one clean-machine troubleshooting case with a reproducible fixture.

Before opening any of these, verify that no equivalent issue or PR already exists.
