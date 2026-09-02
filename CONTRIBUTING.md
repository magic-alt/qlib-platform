# Contributing to qlib-platform

Thank you for helping improve `qlib-platform`.

This repository is not only a collection of research scripts. It is an auditable research system built around immutable data identity, reproducible experiments and an explicit boundary between research and execution. Contributions should preserve those invariants.

## Start here

Before changing code or documentation, read:

1. [README.md](README.md) — project scope and architecture at a glance.
2. [docs/current_state.md](docs/current_state.md) — current governance state and active research restrictions.
3. [docs/architecture.md](docs/architecture.md) — system layers and failure model.
4. [docs/architecture_boundary.md](docs/architecture_boundary.md) — Research Plane / Execution Plane ownership.
5. [docs/identity_and_lineage.md](docs/identity_and_lineage.md) — immutable identity rules.
6. [AGENTS.md](AGENTS.md) — repository guidance used by coding agents and maintainers.

If your change touches contracts, data identity, research governance, promotion, live inference, artifact export or execution handoff, these documents are part of the implementation contract rather than optional background reading.

## Development environment

The supported Python range is `>=3.10,<3.13`; Python 3.12 is the recommended local interpreter.

### Linux / macOS

```bash
git clone https://github.com/magic-alt/qlib-platform.git
cd qlib-platform
python3.12 -m venv .venv
RepoPython=.venv/bin/python
$RepoPython -m pip install --upgrade pip
$RepoPython -m pip install -c constraints/ci.txt -e '.[dev]'
```

### Windows PowerShell

```powershell
git clone https://github.com/magic-alt/qlib-platform.git
cd qlib-platform
python3.12 -m venv .venv
$RepoPython = '.\.venv\Scripts\python.exe'
& $RepoPython -m pip install --upgrade pip
& $RepoPython -m pip install -c constraints/ci.txt -e ".[dev]"
```

Prefer the repository-local interpreter for every governed command. Do not rely on a globally installed `python`, `tq` or `qrun` when validating changes.

## Change classification

Classify the change before implementing it. The classification determines the review depth and validation evidence expected in the pull request.

| Change class | Typical examples | Review emphasis |
| --- | --- | --- |
| **Documentation** | README, runbooks, diagrams, examples | accuracy, links, current-vs-frozen status, no duplicated moving governance facts |
| **Research** | features, models, diagnostics, portfolio policies | causal timing, leakage prevention, fold isolation, reproducibility, artifact identity |
| **Data** | ingestion, PIT transforms, calendars, releases, dataset materialization | source identity, as-of semantics, adjustment rules, hashes, lineage, fail-closed behavior |
| **Contract** | schemas, Artifact Contract v2, identity manifests | compatibility, validation, parent binding, versioning, downstream impact |
| **Operations** | health, outbox, scheduler, recovery, deployment | idempotency, retry semantics, durability, observability, explicit state changes |
| **Boundary-sensitive** | model deployment, live inference, target portfolio handoff | no broker-state ownership, no execution-semantic leakage into the Research Plane |

A change may belong to more than one class.

## Core invariants

Contributions must preserve the following unless the change explicitly proposes and documents a new contract:

- **Immutable identity:** published releases, datasets, snapshots and evidence are not repaired in place to make verification pass.
- **Explicit lineage:** downstream artifacts identify the upstream data/configuration/research inputs they depend on.
- **PIT correctness:** information must not be available before its causal publication/effective time.
- **Fold isolation:** walk-forward research must not leak validation, future labels or holdout information into training or selection.
- **Governed holdout:** parser availability does not authorize holdout access or publishing.
- **Fail closed on integrity:** schema, hash, identity, causal timing and required capability mismatches block the operation.
- **Fail soft on optional availability:** an optional external dependency being unavailable must not silently corrupt local research state.
- **Research/execution boundary:** this repository may produce target portfolios, but it does not own broker orders, fills, positions or ledger state.

## Branch and pull-request workflow

Use a focused branch name, for example:

```text
fix/pit-release-verification
feat/regime-diagnostics
docs/quick-start
chore/ci-python-matrix
```

Keep pull requests reviewable. Prefer one coherent behavioral change over a broad mixture of refactoring, feature work and documentation cleanup.

A strong pull request should answer:

- What problem is being solved?
- Which change class(es) apply?
- Which invariants or contracts could be affected?
- What was changed and why was this design chosen?
- What validation was run?
- Were any immutable artifacts, evidence directories or governed states created?
- Which documentation was updated?
- Are there compatibility or migration implications?

Use the repository pull-request template; do not delete checklist items just because they are inconvenient. Mark non-applicable items explicitly.

## Required validation

Run the checks relevant to your change using the repository interpreter.

Baseline validation:

```bash
$RepoPython scripts/check_docs.py --root .
$RepoPython -m ruff check src tests
$RepoPython -m ruff format --check src tests
$RepoPython -m mypy src
$RepoPython -m pytest
```

Contract validation when applicable:

```bash
$RepoPython -m tushare_qlib \
  --config configs/pipeline.integrated.yaml \
  validate-qrun-contract
```

Coverage can be inspected with:

```bash
$RepoPython -m pytest --cov=src/tushare_qlib --cov-report=term-missing
```

Do not run state-changing or evidence-producing commands merely to make a pull request look more thoroughly tested. If a command writes an immutable evidence directory, publishes/promotes state, refits/deploys a model, emits a live signal or drains an outbox, the exact input/output scope must be intentional and disclosed in the PR.

## Tests

Add or update tests for behavior changes.

Good tests in this repository should emphasize observable contracts rather than implementation trivia. Depending on the change, consider covering:

- deterministic identity generation;
- malformed or mismatched parent references;
- PIT/as-of timing boundaries;
- fold and holdout isolation;
- invalid configuration fail-closed behavior;
- portfolio policy replay consistency;
- artifact schema and hash verification;
- idempotent retry/recovery behavior;
- standalone mode when optional services are unavailable.

Bug fixes should normally include a regression test that fails without the fix.

## Documentation rules

Documentation is part of the governed surface.

### Active vs frozen documentation

- `docs/current_state.md` is the source of truth for fast-changing governance state.
- Active architecture, configuration, CLI and operations documentation should describe current behavior.
- Frozen certification, acceptance and historical research documents stay bound to their documented baseline.
- Historical material under `docs/history/` must not be presented as current operating instructions.

Do not copy moving facts such as the current reviewed SHA, active research phase, holdout authorization or publishing authorization into multiple documents. Link to `docs/current_state.md` instead.

### Style

Prefer:

- short descriptive headings;
- one concept per paragraph;
- tables for stable comparisons, not prose walls;
- Mermaid for architecture/data-flow diagrams when the relationship matters;
- repository-relative links for internal documents;
- exact CLI examples that match the current parser;
- explicit labels such as **Research Plane**, **Execution Plane**, **DataRelease**, **DatasetVersion** and **TARGET_PORTFOLIO** when those identities matter.

Avoid decorative complexity that makes Markdown harder to maintain. GitHub Markdown should remain readable in source form.

## Commit messages

Use concise, intent-oriented messages. Conventional prefixes are encouraged because the repository already uses them:

```text
feat: ...
fix: ...
docs: ...
test: ...
refactor: ...
chore: ...
```

Examples:

```text
fix(data): enforce PIT release parent binding
docs: clarify standalone quick start
test(research): cover final-holdout isolation
```

## Issues

Use the structured issue templates when possible. Include reproducible inputs and non-sensitive logs, but never publish:

- TuShare tokens;
- broker/QMT credentials;
- account identifiers;
- private keys;
- `.env` contents;
- proprietary datasets that cannot be redistributed;
- exploit details for a suspected security vulnerability.

Security-sensitive reports belong under [SECURITY.md](SECURITY.md).

## Scope boundaries

This repository welcomes improvements to the research platform, but some proposals belong elsewhere.

Changes whose primary responsibility is authoritative execution, hard risk, OMS, live order lifecycle, broker fills, positions or ledger state belong in [`magic-alt/platform`](https://github.com/magic-alt/platform), not here.

When in doubt, preserve the boundary and propose an artifact/contract handoff instead of importing execution state into the Research Plane.
