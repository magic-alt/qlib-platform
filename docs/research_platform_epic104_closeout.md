---
status: ACTIVE
owner: architecture
applies_to_commit: 86b0bac9bd9f2d54fd2a2654b8196eb7d6a54639
last_verified: 2026-09-15
---

# Research Platform Epic #104 closeout

Issue #104 is the September 2026 research-platform audit Epic. This page closes the **qlib-platform software scope** after child issues #105 through #110 were completed and merged. It is not a production-trading certificate and it does not close the paired execution-platform Epic `magic-alt/lean-local-platform#58`.

The machine-readable source of truth for this closeout is `contracts/research-platform-epic104.v1.json`. CI verifies that the manifest continues to match the runtime contract versions, research profiles, Strategy SDK registry, data-source error taxonomy and governance boundary.

## Verdict

`qlib-platform` now has the audited research-side foundations requested by #104:

- provider-neutral data semantics on top of the existing provider registry;
- versioned research profiles for A-share equity and the first ETF slice;
- Artifact Contract v3 with v2 compatibility and a single DataRelease-bound target handoff;
- governed research planning, matrix execution, deterministic resume and leakage guards;
- frozen `topk_dropout_v1` / `rank_buffer_v1` baselines, shadow-only strategy evaluation and strategy-versus-execution attribution;
- a stable Research / Alpha Factory boundary that leaves broker, OMS, hard-risk, authoritative fills/positions/ledger, Paper and Live authority outside this repository.

The paired LEAN work remains external evidence. `qlib-platform` may prove producer-side contract conformance and maintain compatibility fixtures, but it must not infer execution certification from that fact.

## Epic acceptance matrix

| #104 acceptance item | Closeout | Evidence / interpretation |
| --- | --- | --- |
| Child tasks have fixed code anchors, positive/negative tests and compatibility/rollback semantics | **Complete** | #105–#110 are complete. Their implementation anchors are recorded in the closeout manifest and remain under repository CI. |
| Frozen A-share data/config/strategy remains replayable without rewriting old release identity | **Complete** | DataRelease identity remains immutable; TopK/RankBuffer versioned IDs are frozen and covered by golden behavior tests. |
| Producer/consumer compatibility fails closed for bad release/hash/invalid input | **Research contract complete; execution evidence external** | Artifact v3/v2 compatibility and producer checks are implemented here. Independent LEAN consumer/Paper/Live certification remains owned by `lean-local-platform#58`. |
| Capability levels distinguish research/backtest/paper/live | **Complete** | The closeout contract permits only research/backtest/diagnostics/shadow here and explicitly forbids paper/live/production authority. |
| Platformization must not relax research gates or open the final holdout | **Complete** | Formal candidates, model selection, publishing and final-holdout access remain disabled by the active Phase 3-D governance state. |

## Capability maturity

### Multi-asset / multi-market

The implemented and tested research profiles are `ashare_equity_v1` and `ashare_etf_v1`. This is the first multi-asset slice, not a claim that HK/US equities, FX, convertible bonds, futures or options are production-ready. New assets must add a versioned ResearchProfile plus asset-specific evidence; metadata or an API template is not tradability proof.

### Multi-source data

`DatasetRequest`, `CanonicalBatch`, capability negotiation, provenance and distinct failure classes make the research pipeline provider-neutral. A provider implementation may still have its own endpoint/pagination/authentication details, but those details must stay inside the adapter. Cross-source substitution requires new lineage/release evidence and must not silently patch one release with another provider.

### Artifact / LEAN handoff

Artifact Contract `3.0` is current and `2.0` is the supported previous version. The research side exports a single DataRelease-bound target contract; it does not grant itself `LEAN_VALIDATED`, `PAPER` or `PRODUCTION` lifecycle authority. The execution repository must independently validate compatibility and produce its own acceptance evidence.

### Strategy pipeline

`topk_dropout_v1` and `rank_buffer_v1` are immutable baseline families. `score_threshold_shadow_v1` is evaluation-only: it cannot become a formal candidate and it cannot enable search. Portfolio weighting, rebalance and research-cost assumptions are explicit descriptor components. Uncalibrated cost attribution remains unavailable rather than being inferred from observed fills.

## Compatibility and rollback rules

The closeout deliberately preserves existing identities and semantics:

1. Existing DataRelease identities are immutable and are never rewritten during migration or replay.
2. Existing strategy IDs are versioned behavior anchors; extensions use new IDs rather than redefining old ones.
3. Artifact v2 remains a previous supported contract. v3 adds semantics/versioning instead of reinterpreting v2 payloads in place.
4. ResearchProfile promotion scope remains `research_only`; a profile cannot authorize execution.
5. Failure or outage of the execution platform never moves broker/OMS/ledger authority into `qlib-platform`.
6. A future Paper/Live capability requires execution-side evidence and an explicit governance change; this Epic does not provide either.

## External evidence boundary

The paired execution Epic is `magic-alt/lean-local-platform#58`. Its remaining work can change whether a given Artifact version, asset, broker path or runtime is certified by the execution plane, but it does not invalidate the completed research-side software interfaces recorded here.

Until that execution evidence exists, the correct state is **research software complete / execution certification pending**. This distinction is intentional and fail-closed.
