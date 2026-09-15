---
status: ACTIVE
owner: research
applies_to_commit: d6cd68af6646727ea36c6a6e72d681bc8ae73e62
last_verified: 2026-09-15
---

# Artifact Contract v3

Artifact Contract v3 is an additive, negotiated successor to the frozen v2 Qlib research handoff. It does not reinterpret v2 payloads in place and it does not grant Qlib execution authority.

## Version policy

The producer capability set is ordered as `3.0, 2.0`. Negotiation selects the highest mutually supported version. `3.0/2.0 -> 2.0` is the supported N/N-1 downgrade path; a v2-only producer consumed by a v3+v2 consumer is the reverse-upgrade case. Unknown versions such as `1.0` are rejected instead of repaired or silently coerced.

The frozen v2 golden bundle remains the compatibility anchor. A v3 rollout must not refresh or reinterpret the v2 golden.

## Target instructions

v3 separates execution-facing intent from empty payload accidents.

`TARGETS + FULL_SNAPSHOT` means the target rows describe the complete desired security state. Missing instruments mean zero target weight, expressed as `omittedInstrumentPolicy=ZERO_TARGET`. `cashWeight` is explicit.

`TARGETS + DELTA` means rows are weight changes relative to the consumer's current state. Missing instruments are unchanged, expressed as `omittedInstrumentPolicy=UNCHANGED`. `cashWeightDelta` is explicit and `cashWeight` is absent.

`CASH_ONLY` is an explicit full-snapshot instruction with no security rows and `cashWeight=1`. It is not represented by a missing file or an empty generic target list.

`NO_SIGNAL` means no new rebalance instruction exists. It preserves current state and cannot supersede a prior target.

`REVOKE` explicitly invalidates a prior artifact through `supersedesArtifactId`. It is a control instruction, not an executable target portfolio.

## Time contract

All timestamps are timezone-aware ISO-8601 values. The causal ordering is:

`asOfTime <= signalTime <= producedAt`

and

`signalTime <= validFrom <= tradeNotBefore < validUntil`.

A consumer observing the payload rejects future-produced and expired instructions. Execution must additionally wait until `tradeNotBefore`. This contract uses absolute timestamps rather than assuming that equal local dates imply equal information availability.

## Currency and valuation

Every instruction declares `baseCurrency` and `valuationBasis`. Each target declares its own currency.

A non-base-currency target requires an explicit positive `fxRateToBase`, timezone-aware `fxAvailableAt`, and `fxQuoteId`. FX evidence must be available no later than `asOfTime` and no older than `fx.maxAgeSeconds`. Unknown, stale, or future FX fails closed. Base-currency targets must not attach a synthetic FX conversion.

This deliberately does not infer inverse or triangular FX rates.

## Policy, calendar and lineage identity

The v3 contract carries content hashes for the strategy policy and calendar version. Artifact identity also binds DataRelease, UniverseRelease, source manifest hash, payload hash, parent artifact IDs, and the contract identity block. Changing policy, calendar, universe, or parent lineage therefore creates a different identity instead of mutating an existing artifact in place.

Payload references are relative JSON paths with content hashes. Absolute paths, traversal, executable/pickle media types, and unsafe path forms are rejected by the shared contract helper. The v2 local `manifest_path` convention remains a separate legacy/local concern.

## Long/short and exposure

Target weights are signed, finite values in `[-1, 1]`. A full snapshot computes gross and net exposure and must remain within `maxGrossExposure` and `maxAbsNetExposure`.

For a delta instruction, the contract records gross/net delta rather than pretending the producer can validate final exposure without the consumer's current holdings. The execution platform must apply the delta to its owned state and re-run its independent risk checks before LEAN validation or Paper promotion.

## Promotion boundary

Qlib artifacts remain research-owned and may reach at most `RESEARCH_PROMOTED`. Artifact Contract v3 does not authorize `LEAN_VALIDATED`, `PAPER`, `PRODUCTION`, broker routing, or use of the sealed final holdout.

The consumer implementation in `lean-local-platform` must remain independent rather than importing the Qlib validator. Shared JSON fixtures define expected accept/reject behavior, while each repository owns its own implementation and side-effect guarantees.

## Qlib v3 producer

The producer entry point is `export_manifest_as_v3_bundle(...)` in `qlib_platform.artifacts.research_bundle_export`. It intentionally does **not** enqueue the bundle into the Artifact Outbox yet; delivery stays disabled until the independent LEAN v3 consumer and the cross-repository compatibility gate are merged.

A source research manifest must carry an explicit v3 block. The producer does not infer execution-facing semantics from `latestTargets`, local dates, broker defaults, or a legacy v2 payload:

```json
{
  "artifactContractV3": {
    "schemaVersion": "3.0",
    "strategyPolicyId": "cross_market_fixture_policy_v1",
    "parentArtifactIds": ["art_<64-lowercase-hex>"],
    "targetInstruction": {
      "schemaVersion": "3.0",
      "instructionType": "TARGETS",
      "targetSemantics": "FULL_SNAPSHOT",
      "omittedInstrumentPolicy": "ZERO_TARGET"
    }
  }
}
```

`targetInstruction` must contain the complete calendar, policy hash, causal timestamps, risk limits, currency/FX evidence and target/control-state fields defined above. `DataReleaseId` and `UniverseReleaseId` continue to resolve through the repository's canonical research-manifest lineage resolver; conflicts fail closed.

Example producer call:

```python
from qlib_platform.artifacts.research_bundle_export import export_manifest_as_v3_bundle

manifest = export_manifest_as_v3_bundle(
    "outputs/research/<run>/manifest.json",
    "outputs/handoff/<run>/artifact-v3",
    git_commit="<full-git-sha>",
    container_digest="sha256:<64-lowercase-hex>",
)
```

Before any final bundle directory is published, the producer canonicalizes and validates the entire instruction, computes every payload hash and artifact identity, and builds the complete expected file set in memory. Files are then written to a sibling staging directory and atomically renamed into place. An existing byte-identical directory is an idempotent retry; an existing non-identical directory is rejected and never deleted or repaired.

The byte-lock fixture is `tests/fixtures/artifact_v3/producer_golden_sha256.json`. It freezes every emitted file SHA-256 and the aggregate bundle digest. v2 golden fixtures are not regenerated by this producer.

### Migration from v2

Legacy research manifests without `artifactContractV3` must continue to use `export_manifest_as_v2_bundle(...)`. The v3 producer deliberately rejects them rather than synthesizing calendar, FX, cash, no-signal/revoke, or snapshot/delta semantics. This keeps the migration opt-in and prevents a silent semantic upgrade.

## Rollout sequence for #108

1. **Shared contract PR (#123):** frozen v3 target semantics, temporal/FX rules, portable references, identity rules, and the N/N-1 matrix while preserving v2.
2. **Qlib producer PR (current slice):** wire the immutable exporter, byte-lock its golden bundle, and prove invalid input cannot leave partial final output.
3. **LEAN consumer PR (next slice):** independently validate the same fixtures and prove rejection has no registry/dispatch/order side effects.
4. **Cross-repository gate:** exercise producer N / consumer N, producer N / consumer N-1, producer N-1 / consumer N, and no-common-version rejection.
5. Close #108 only after producer, consumer, and compatibility-gate PRs are merged and green.
