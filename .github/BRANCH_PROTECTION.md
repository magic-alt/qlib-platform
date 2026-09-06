# `main` branch governance baseline

`qlib-platform` treats the default branch as part of the auditable research control plane. Source governance must therefore fail closed in the same way as dataset, experiment, artifact, and promotion governance.

## Required repository ruleset

Create one active repository branch ruleset named `main` and target the default branch (`~DEFAULT_BRANCH`, or explicitly `refs/heads/main`). Do not leave the include list empty.

Required branch rules:

- Restrict deletions.
- Block non-fast-forward updates / force pushes.
- Require a pull request before merging.
- Require all review conversations to be resolved before merging.
- Require branches to be up to date before merging when GitHub offers that option for the selected status-check rule.
- Do not configure a bypass actor for routine development. Emergency bypasses, if ever introduced, must be separately audited.

For a single-maintainer repository, `required_approving_review_count = 0` is acceptable: the important invariant is that changes flow through a PR and cannot be pushed directly to `main`. Increase the approval count when another maintainer is available.

## Required status checks

The following check contexts are intentionally stable and should be configured as required:

- `CI / required-ci`
- `Qlib Capability Contract / qlib-required`
- `CodeQL / Analyze (python)`
- `Dependency Review / dependency-review`
- `Docs / build`
- `Release Check / package-sbom`

`required-ci` aggregates the conditional OS/Python matrix, clean-machine wheel checks, standalone isolation, quality/certification, and governance jobs. `qlib-required` aggregates the Qlib core surface, model-zoo surface, explicit upstream exceptions, and the real Qlib 0.9.7 Alpha158/LightGBM end-to-end parity test. Rulesets should require these aggregators rather than individual matrix members so normal CI refactors do not silently weaken or permanently wedge branch protection.

## Repository-wide coverage invariant

The blocking `CI / quality` path must execute:

```bash
python scripts/run_comprehensive_checks.py \
  --skip-governance \
  --coverage-threshold 85
```

The comprehensive runner is the single source of truth for repository-wide statement coverage. Do not add a second lower `pytest --cov-fail-under` value to GitHub Actions.

## Verification after changing GitHub settings

After saving the ruleset:

1. Open the repository Rules page and confirm the rule targets the default branch rather than an empty ref set.
2. Open `main` and confirm direct pushes are rejected by the ruleset.
3. Open a small PR and confirm GitHub lists all six required checks above.
4. Confirm merge is disabled while any required check is pending or failing.
5. Confirm the PR becomes mergeable only after the required checks succeed and conversations are resolved.

The repository ruleset is an administrator-side GitHub setting and cannot be enforced solely by files committed to the repository. This document defines the desired state so it can be reviewed and audited alongside the code that emits the required check contexts.
