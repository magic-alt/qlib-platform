# Research package architecture

`qlib_platform.research` is organized by durable research responsibility rather than historical experiment stage.

- `contracts/`: frozen candidate and stability design contracts.
- `evidence/`: evidence collection and data-release acceptance.
- `features/`: feature stores, taxonomies, clusters, and candidate feature sets.
- `hypotheses/`: pre-registered hypothesis bindings.
- `workflow/`: baseline, candidate, stability, training, timing, and walk-forward orchestration.
- `evaluation/`: candidate statistics, selection, promotion gates, and walk-forward acceptance.
- `diagnostics/`: stability, decay, regimes, attribution, explanation, and portability analysis.
- `studies/`: alpha, regime, attribution, explanation, and synthesis study composition.
- `portfolio/`: bounded portfolio overlays.
- `reporting/`: synthesis payloads and research summaries.
- `artifacts/`: immutable research artifact I/O.
- `interfaces/`: research-facing interface helpers.

Runtime Python identifiers and implementation-hash paths use the responsibility-oriented layout. Historical stage identifiers may remain inside immutable artifact schema values or governance state where changing them would break lineage. They must not be used as Python module boundaries, import paths, filenames, or CLI command names.
