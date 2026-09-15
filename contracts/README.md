# Vendored and local governance contracts

The vendored schemas below record cross-repository exchange contracts. Their presence does not by itself prove that every local exporter field is validated against the complete upstream schema or that an execution consumer is production-certified.

| Contract | Upstream revision | Local SHA-256 |
| --- | --- | --- |
| `data-release-v1.schema.json` | `fd56480` | `a1650c0d90e656a3ba13c9215074b9bcbb589c8eb7458d9dce82e2c5e07aa4e3` |
| `qlib-research-artifact-v2.schema.json` | `fd56480` | `56b5b26c13e2095fb25bd50c0644a64a3863e2ab2be080e2f8a42da952b470e7` |

Last upstream verification for the vendored v2 schemas: 2026-08-28. Update the schemas, revision, hashes, contract tests, and this receipt together whenever the vendored platform contract changes.

## Local governance contracts

`research-platform-epic104.v1.json` is a qlib-platform governance/closeout contract, not a wire protocol. It records the completed research-side capabilities from Audit Epic #104 and explicitly keeps Paper/Live/Production authority in the execution repository. CI cross-checks it against current Artifact, ResearchProfile, Strategy SDK and DataSource contracts.

Artifact Contract v3 is implemented in `src/qlib_platform/artifacts/artifact_contract_v3.py` and documented in `docs/artifact_contract_v3.md`. v3 is additive and negotiates with the frozen v2 compatibility surface; this README does not replace the runtime validator or producer/consumer compatibility tests.
