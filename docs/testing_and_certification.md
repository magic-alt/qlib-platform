---
status: ACTIVE
owner: architecture
applies_to_commit: 8692afefe1f6cc82ab1f276fca788888f9f30f3e
last_verified: 2026-08-28
---

# Testing and Certification

## Local baseline

```powershell
$RepoPython = '.\.venv\Scripts\python.exe'
& $RepoPython scripts/check_docs.py --root .
& $RepoPython -m ruff check src tests
& $RepoPython -m ruff format --check src tests
& $RepoPython -m mypy src
& $RepoPython -m tushare_qlib --config configs/pipeline.integrated.yaml validate-qrun-contract
& $RepoPython -m pytest --cov=src/tushare_qlib --cov-report=term-missing --cov-fail-under=60
```

Use targeted deterministic tests first. Add failure-injection coverage when a change affects PIT, identity,
lineage, snapshots, checkpoints, folds, holdout or execution boundaries.

## Certification scope

The certified infrastructure baseline is `4f5c5d5`, not the current main as a whole. Certification
covers the scope recorded in [Research Infrastructure Certification](research_infrastructure_certification.md)
and the frozen [Full Walk-forward Acceptance](full_walk_forward_acceptance.md).

Material changes to data processing, feature semantics, splits, caches, checkpoints, prediction snapshots,
portfolio accounting or acceptance logic require incremental revalidation. Documentation-only changes do
not silently extend the certified code scope.

## Documentation baseline

`scripts/check_docs.py` checks governed document front matter, internal links, CLI references, portable
interpreter/workflow paths, version pins, historical entry isolation and the execution-plane boundary.
CI runs it separately and `project-audit` aggregates its findings.
