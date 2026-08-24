---
name: phase3-diagnostics
description: Work on or audit the frozen A-share Phase 3-D P3-D00 through P3-D04 stability and regime diagnostics without creating candidates, selecting models, accessing holdout, or publishing.
---

# Phase 3-D diagnostics

This skill governs the active `ashare_alpha_stability_phase3_v1` program. Before acting, read:

- `docs/research_infrastructure_certification.md`
- `docs/alpha_research_phase_3.md`
- `configs/research/ashare_phase3_v1.yaml`
- `src/tushare_qlib/research/phase3_contract.py`
- `src/tushare_qlib/research/phase3_program.py`
- `src/tushare_qlib/research/phase3_diagnostics.py`
- `src/tushare_qlib/research/phase3_portability.py`
- the applicable `tests/test_phase3_*.py` and `tests/failure_injection/test_phase3_contract_failures.py`

Phase 3-D is diagnostics only. The permitted scope is P3-D00 through P3-D04:

- D00: freeze and bind the Phase 2 acceptance/evidence, DataRelease-v2 acceptance, contract lock, DataRelease, DatasetVersion, FeatureSnapshot, labels, anchor PredictionSnapshots, regime specification, source revision, and implementation hashes.
- D01: derive daily IC/RankIC, TopK forward-label spread, turnover, rolling stability, and negative rolling-RankIC episodes.
- D02: derive diagnostics from the existing causal regime engine.
- D03: report descriptive pre/post transition windows; never promote them automatically to confirmatory tests.
- D04: report model-vintage decay by sessions since each fold test began; do not turn it into a training-window experiment.

Do not register a formal hypothesis, create a Research Candidate, select or promote a model, run P2-R01 through P2-R03, access/open the final holdout, authorize publishing, or synthesize portfolio P&L from labels. Keep `formalCandidatesAllowed=false`, `publishingAuthorized=false`, and `finalHoldout.accessAllowed=false`.

The anchors, comparisons, rolling/transition windows, and causal regime specification in `ashare_phase3_v1.yaml` are governed definitions. Do not change them to improve diagnostics or make tests pass. A verifier must remain non-retraining and fail closed on symlinks/path escapes, extra or missing files, source revision drift, checksum/lineage drift, holdout access, candidate creation, or publishing authorization.

`phase3-diagnose` writes an immutable evidence directory. Treat running it or producing a diagnostic bundle as state-changing: obtain explicit user authorization and confirm the exact output directory first.
