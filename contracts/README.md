# Vendored platform contracts

These schemas are vendored from `magic-alt/platform` Artifact Contract v2. Their
presence records the upstream exchange contract; it does not by itself prove that
every local exporter field is validated against the complete upstream schema.

| Contract | Upstream revision | Local SHA-256 |
| --- | --- | --- |
| `data-release-v1.schema.json` | `fd56480` | `a1650c0d90e656a3ba13c9215074b9bcbb589c8eb7458d9dce82e2c5e07aa4e3` |
| `qlib-research-artifact-v2.schema.json` | `fd56480` | `56b5b26c13e2095fb25bd50c0644a64a3863e2ab2be080e2f8a42da952b470e7` |

Last upstream verification: 2026-08-28. Update the schemas, revision, hashes,
contract tests, and this receipt together whenever the platform contract changes.
