# A-share Phase 3-D profile

Read this reference for any work on the frozen `ashare_alpha_stability_phase3_v1` program.

Before acting, read:

- `docs/research_infrastructure_certification.md`
- `docs/alpha_research_stability.md`
- `configs/research/ashare_stability_diagnostics_v1.yaml`
- `src/qlib_platform/research/contracts/stability_program.py`
- `src/qlib_platform/research/workflow/stability_program.py`
- `src/qlib_platform/research/diagnostics/stability.py`
- `src/qlib_platform/research/diagnostics/portability.py`
- the applicable `tests/test_stability_*.py` files
- `tests/failure_injection/test_stability_contract_failures.py`

Phase 3-D is diagnostics only. The permitted scope is P3-D00 through P3-D04:

- D00 freezes and binds the Phase 2 acceptance/evidence, DataRelease-v2 acceptance, contract lock, DataRelease, DatasetVersion, FeatureSnapshot, labels, anchor PredictionSnapshots, regime specification, source revision, and implementation hashes.
- D01 derives daily IC/RankIC, TopK forward-label spread, turnover, rolling stability, and negative rolling-RankIC episodes.
- D02 derives diagnostics from the existing causal regime engine.
- D03 reports descriptive pre/post transition windows; never promote them automatically to confirmatory tests.
- D04 reports model-vintage decay by sessions since each fold test began; do not turn it into a training-window experiment.

Do not register a formal hypothesis, create a Research Candidate, select or promote a model, run P2-R01 through P2-R03, access or open the final holdout, authorize publishing, or synthesize portfolio P&L from labels. Keep `formalCandidatesAllowed=false`, `publishingAuthorized=false`, and `finalHoldout.accessAllowed=false`.

The anchors, comparisons, rolling/transition windows, and causal regime specification in `ashare_phase3_v1.yaml` are frozen governed definitions. A verifier must remain non-retraining and fail closed on symlinks or path escapes, extra or missing files, source revision drift, checksum or lineage drift, holdout access, candidate creation, or publishing authorization.

`stability-diagnose` writes an immutable evidence directory. Obtain explicit user authorization and confirm the exact output directory before running it or producing a diagnostic bundle.
