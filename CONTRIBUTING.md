# Contributing to qlib-platform

Thank you for contributing to `qlib-platform`.

This repository is an auditable quantitative-research system, not a collection of isolated notebooks. Changes are reviewed not only for code quality, but also for **causal correctness, reproducibility, lineage, failure behavior, and the Research Plane / Execution Plane boundary**.

Please read the [Code of Conduct](CODE_OF_CONDUCT.md) before participating.

## Choose a contribution path

| You want to... | Start here |
| --- | --- |
| Fix a small bug or documentation issue | look for [`good first issue`](https://github.com/magic-alt/qlib-platform/labels/good%20first%20issue) or [`help wanted`](https://github.com/magic-alt/qlib-platform/labels/help%20wanted) |
| Propose a feature | open the structured feature-request form before a large implementation |
| Change research methodology | read `docs/current_state.md`, `docs/research_lifecycle.md`, and the relevant frozen/active protocol |
| Change data identity, PIT logic or contracts | read `docs/identity_and_lineage.md` and `docs/artifact_contract_v2.md` |
| Change execution handoff behavior | read `docs/architecture_boundary.md` first |
| Report a vulnerability or secret exposure | follow `SECURITY.md`; do **not** open a public issue |

First-time contributors should also read the [Good First Issue guide](docs/project/good-first-issues.md).

## Before you code

Read these in order:

1. [README.md](README.md) — scope and architecture at a glance.
2. [docs/current_state.md](docs/current_state.md) — active governance state.
3. [docs/architecture.md](docs/architecture.md) — logical layers and failure model.
4. [docs/identity_and_lineage.md](docs/identity_and_lineage.md) — immutable identity rules.
5. [AGENTS.md](AGENTS.md) — repository-specific guidance for maintainers and coding agents.

If your change touches contracts, research promotion, sealed holdouts, live inference, artifact export, or the execution boundary, the relevant documents are part of the implementation contract.

## Development setup

Supported Python: `>=3.10,<3.13`. Python 3.12 is the recommended local interpreter.

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

Use the repository-local interpreter for governed commands. Do not validate a PR with an unrelated global `python`, `tq`, or `qrun`.

## Classify the change

Every PR should identify its risk class. A change may have more than one.

| Class | Examples | Primary review concerns |
| --- | --- | --- |
| **Docs / community** | docs, examples, issue forms, branding | accuracy, links, authority/status, no stale governance facts |
| **Code / refactor** | internal implementation without intended behavior change | compatibility, regression risk, test coverage |
| **Research** | features, labels, models, diagnostics, portfolio policies | causal timing, leakage, fold isolation, reproducibility |
| **Data** | ingestion, PIT transforms, calendars, releases, materialization | source identity, as-of semantics, hashes, lineage |
| **Contract** | schemas, manifests, Artifact Contract v2 | compatibility, parent binding, migration, downstream impact |
| **Operations** | health, scheduler, outbox, recovery | idempotency, durability, retries, observability |
| **Boundary-sensitive** | live inference, target-portfolio handoff | no broker-state ownership or execution-semantic leakage |
| **Security / supply chain** | workflows, dependencies, release pipeline | least privilege, pinning, provenance, secret exposure |

## Non-negotiable invariants

Unless a PR explicitly proposes a reviewed contract change, it must preserve:

- **Immutable identity** — published releases, datasets, snapshots, and evidence are never repaired in place.
- **Explicit lineage** — downstream artifacts identify their material upstream inputs.
- **PIT correctness** — information is unavailable before its causal publication/effective time.
- **Fold and holdout isolation** — future labels, validation, and sealed holdout data cannot influence earlier stages.
- **Fail closed on integrity** — schema, hash, identity, timing, or required-capability mismatches block the operation.
- **Fail soft only on optional availability** — optional service outages must not corrupt local research state.
- **Research/execution separation** — this repository may publish a target portfolio; it does not own broker orders, fills, positions, or ledger state.

## Branch and pull-request workflow

Use a focused branch:

```text
fix/pit-release-verification
feat/regime-diagnostics
docs/quick-start
chore/dependency-policy
```

Prefer one coherent change over a large mixed PR. A reviewable PR should explain:

- the problem and intended outcome;
- applicable change class(es);
- contracts or invariants at risk;
- implementation and design trade-offs;
- tests and checks executed;
- any state-changing or evidence-producing commands that were run;
- documentation and migration impact.

Use the PR template and mark non-applicable checklist items rather than deleting them.

### Review expectations

Maintainers review higher-risk changes more strictly than low-risk changes. Expect requests for stronger tests or evidence when a PR affects PIT timing, identity derivation, holdout access, promotion, artifact contracts, or execution handoff.

A reviewer may reject a change that produces better backtest results if the methodology, lineage, or causal timing is weaker.

## Validation

Run the smallest set that proves your change while preserving the repository's safety model.

### Baseline

```bash
$RepoPython scripts/check_docs.py --root .
$RepoPython -m ruff check src tests
$RepoPython -m ruff format --check src tests
$RepoPython -m mypy src
$RepoPython -m pytest
```

### Contract-sensitive changes

```bash
$RepoPython -m qlib_platform \
  --config configs/pipeline.integrated.yaml \
  validate-qrun-contract
```

### Documentation-site changes

```bash
$RepoPython -m pip install -e '.[docs]'
$RepoPython -m mkdocs build --strict
```

### Coverage

```bash
$RepoPython -m pytest --cov=src/qlib_platform --cov-report=term-missing
```

CI is authoritative for the merge gate. Local success is necessary evidence, not a substitute for required GitHub checks.

## Tests

Behavior changes should add or update tests. Bug fixes should normally include a regression test.

High-value tests cover observable contracts such as:

- deterministic identity generation;
- malformed or mismatched parent references;
- PIT/as-of timing boundaries;
- fold and holdout isolation;
- invalid configuration failing closed;
- portfolio-policy replay consistency;
- artifact schema/hash verification;
- idempotent retry and recovery;
- standalone operation when optional services are unavailable.

Avoid tests that merely duplicate implementation details.

## Research integrity and side effects

Do not run a state-changing or evidence-producing command simply to make a PR appear better tested.

If a command writes immutable evidence, publishes/promotes state, refits/deploys a model, emits a live signal, or drains an outbox, its input/output scope must be intentional and disclosed in the PR.

Never:

- alter a sealed holdout to improve a result;
- rewrite historical evidence to match new code;
- present exploratory performance as certified performance;
- suppress an integrity failure by silently weakening a gate;
- commit credentials, account identifiers, proprietary data, or secret-bearing logs.

## Documentation

Documentation is part of the governed surface.

- `docs/current_state.md` owns fast-changing governance facts.
- Active architecture, configuration, CLI, and operations docs describe current behavior.
- Frozen certification/acceptance documents remain bound to their baseline.
- `docs/history/` is provenance, not current operating guidance.
- CLI syntax belongs in `docs/cli_reference.md`, not duplicated across high-level pages.
- Run `scripts/check_docs.py --root .` and `python -m mkdocs build --strict` for site-affecting changes.

Prefer exact terminology: **Research Plane**, **Execution Plane**, **DataRelease**, **DatasetVersion**, **FeatureSnapshot**, **PredictionSnapshot**, and **TARGET_PORTFOLIO**.

## Dependencies and generated PRs

Dependabot is the canonical dependency bot for this repository. Do not add Renovate in parallel unless the project deliberately migrates automation; two dependency bots create duplicate update streams.

`pyqlib` and LightGBM are compatibility-sensitive governed dependencies and are upgraded deliberately rather than through unattended version bumps. See `docs/maintainers/repository-governance.md`.

Generated dependency PRs are still subject to CI and review.

## Commit messages

Concise Conventional-Commit-style prefixes are encouraged:

```text
feat: ...
fix: ...
docs: ...
test: ...
refactor: ...
chore: ...
ci: ...
```

Examples:

```text
fix(data): enforce PIT release parent binding
docs: clarify standalone quick start
test(research): cover final-holdout isolation
ci: add dependency review gate
```

## Issues and first contributions

Use the structured issue forms. Before opening a new issue, search for an existing report and verify that the problem belongs to this repository.

A good issue includes:

- expected and actual behavior;
- minimal reproduction or concrete acceptance criteria;
- platform/Python/configuration context when relevant;
- non-sensitive logs or artifact identifiers;
- a clear statement if the issue affects data integrity, PIT timing, security, or governance.

Never put tokens, broker credentials, account identifiers, private keys, `.env` contents, proprietary datasets, or vulnerability exploit details in a public issue.

Maintainers should only apply `good first issue` to tasks that are bounded, low-risk, documented, and independently verifiable. See [Good First Issue policy](docs/project/good-first-issues.md).

## Scope boundary

Changes whose primary responsibility is authoritative execution, hard risk, OMS, live order lifecycle, broker fills, positions, or ledger state belong in [`magic-alt/platform`](https://github.com/magic-alt/platform).

When ownership is ambiguous, preserve the boundary and propose an artifact/contract handoff rather than importing execution state into the Research Plane.
