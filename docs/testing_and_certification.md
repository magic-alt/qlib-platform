---
status: ACTIVE
owner: architecture
applies_to_commit: 4f3f4369b6e55186967bc726bb8dd87fff0e5d70
last_verified: 2026-08-31
---

# Testing and Certification

## Local baseline

```powershell
$RepoPython = '.\.venv\Scripts\python.exe'
& $RepoPython scripts/check_docs.py --root .
& $RepoPython -m ruff check src tests
& $RepoPython -m ruff format --check src tests
& $RepoPython -m mypy src
& $RepoPython -m qlib_platform --config configs/pipeline.integrated.yaml validate-qrun-contract
& $RepoPython -m pytest --cov=src/qlib_platform --cov-report=term-missing --cov-fail-under=60
```

Use targeted deterministic tests first. Add failure-injection coverage when a change affects PIT, identity, lineage, snapshots, checkpoints, folds, holdout or Research/Execution boundaries.

## Certification scope

The certified infrastructure baseline is `4f5c5d5`, not current main as a whole. Certification covers the scope recorded in [Research Infrastructure Certification](research_infrastructure_certification.md) and the frozen [Full Walk-forward Acceptance](full_walk_forward_acceptance.md).

Material changes to data processing, feature semantics, splits, caches, checkpoints, prediction snapshots, portfolio accounting or acceptance logic require incremental revalidation. Documentation-only changes do not silently extend the certified code scope.

Current reviewed/certified/documentation-audit baselines are maintained in [Current State](current_state.md).

## Documentation structural checks

`scripts/check_docs.py` checks governed front matter, internal links, CLI references that match its parser pattern, portable interpreter/workflow paths, version pins, historical entry isolation and the Execution Plane boundary. CI runs it separately and `project-audit` aggregates its findings.

### What `check_docs.py` does not prove

A clean structural docs check is necessary but not sufficient. In particular, it does not guarantee that:

- every bare command name/table contains the current required arguments;
- described command side effects still match implementation behavior;
- an architecture paragraph has not been duplicated or become semantically contradictory;
- a frozen document has not copied a moving current-main fact;
- a newly added code capability has complete operator documentation.

Therefore material documentation reviews must also cross-check the current CLI parser, settings/config profiles, contracts and implementation paths relevant to the claim.

## Documentation review checklist

For an ACTIVE doc change:

1. verify `status`, `owner`, `applies_to_commit` and `last_verified`;
2. run `scripts/check_docs.py --root .`;
3. compare command syntax against `qlib_platform.cli.parser()` / `--help`;
4. classify commands as read-only, verification output, local state-changing or external delivery;
5. verify DataRelease/DatasetVersion and Research/Execution terminology;
6. ensure current-state facts live in `current_state.md`, not frozen/history docs;
7. inspect internal links and the Documentation Index;
8. run targeted tests if the documentation exposed a possible implementation mismatch.

A documentation audit base records what tree was reviewed; it is not an infrastructure certification event.
