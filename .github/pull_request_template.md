## Summary

<!-- What changed? Keep this concise and behavior-focused. -->

## Why

<!-- What problem does this solve? Why is this the right layer/repository for the change? -->

## Change classification

Select all that apply:

- [ ] Documentation
- [ ] Research / model / diagnostics
- [ ] Data / PIT / release / dataset
- [ ] Backtest / portfolio policy
- [ ] Contract / artifact / identity / lineage
- [ ] Operations / health / outbox / recovery
- [ ] Boundary-sensitive / live inference / handoff
- [ ] Tests / CI / developer experience

## Invariants and contracts

<!-- Describe any effect on immutable identity, parent binding, PIT/as-of timing, fold isolation, holdout governance, portfolio-policy replay, Artifact Contract v2 or the Research Plane / Execution Plane boundary. Write "None" only after checking. -->

## Validation

- [ ] `scripts/check_docs.py --root .`
- [ ] `ruff check src tests`
- [ ] `ruff format --check src tests`
- [ ] `mypy src`
- [ ] `pytest`
- [ ] `validate-qrun-contract` when applicable
- [ ] Added/updated regression tests for behavior changes

### Commands / results

```text
Paste the exact relevant validation commands and concise results here.
```

## Governed state / artifact impact

- [ ] No immutable evidence, release, dataset, model deployment, live signal, promotion or outbox state was created/changed solely for validation.
- [ ] Any intentional state-changing operation is listed below with its exact input/output scope.
- [ ] Final holdout / publishing restrictions in `docs/current_state.md` were respected.

<!-- If state changed, describe it here. Otherwise write "None". -->

## Documentation

- [ ] User-facing behavior is documented.
- [ ] CLI examples match the current parser.
- [ ] Fast-changing governance facts are linked to `docs/current_state.md` instead of duplicated.
- [ ] Frozen/history documents were not rewritten as if they described current main.

## Compatibility / migration

<!-- Note schema, configuration, artifact, dataset, CLI or downstream compatibility impact. Write "None" if not applicable. -->

## Reviewer focus

<!-- Point reviewers to the highest-risk files, design decisions or edge cases. -->
