---
status: ACTIVE
owner: architecture
last_verified: 2026-09-02
---

# qlib-platform Documentation

![qlib-platform](assets/brand/qlib-platform-logo.svg)

This site is the canonical navigation surface for `qlib-platform` documentation. Use it to understand the Research Plane, run and extend the platform, and maintain the repository without mixing current operating guidance with frozen research evidence.

> [!IMPORTANT]
> Fast-changing research authorization lives only in [Current Governance State](current_state.md). A roadmap item, GitHub milestone, software release, or available CLI command does not authorize holdout access, research promotion, or production trading.

## Start here

1. **[Architecture](architecture.md)** — Research Plane layers, data flow, and failure model.
2. **[Local Qlib backtest example](https://github.com/magic-alt/qlib-platform/tree/main/examples/local_qlib_backtest)** — smallest maintained runnable Qlib workflow.
3. **[CLI Reference](cli_reference.md)** — command syntax, inputs, outputs, and side effects.
4. **[Current Governance State](current_state.md)** — current research restrictions before governed work.
5. **[Roadmap](project/roadmap.md)** — public engineering direction and milestone criteria.

## Choose your task

| I want to... | Start with | Then read |
| --- | --- | --- |
| Understand the system | [Architecture](architecture.md) | [Architecture Boundary](architecture_boundary.md), [Identity and Lineage](identity_and_lineage.md) |
| Install and configure locally | [README Quick Start](https://github.com/magic-alt/qlib-platform#quick-start) | [Configuration](configuration.md), [Troubleshooting](troubleshooting.md) |
| Run Qlib research | [Local example](https://github.com/magic-alt/qlib-platform/tree/main/examples/local_qlib_backtest) | [Research Lifecycle](research_lifecycle.md), [CLI Reference](cli_reference.md) |
| Work with releases/datasets | [Qlib Data Platform](qlib_data_platform.md) | [Data Schema](data_schema.md), [Identity and Lineage](identity_and_lineage.md) |
| Develop alphas or diagnostics | [Research Lifecycle](research_lifecycle.md) | [Active Phase 3-D](alpha_research_phase_3.md), [Portfolio Policy](portfolio_v2_rank_buffer.md) |
| Operate research jobs | [Operations Runbook](OPERATIONS_RUNBOOK.md) | [Daily Research](operations/daily-research.md), [Recovery](operations/recovery.md) |
| Integrate with `magic-alt/platform` | [Architecture Boundary](architecture_boundary.md) | [Artifact Contract v2](artifact_contract_v2.md) |
| Validate a contribution | [CONTRIBUTING](https://github.com/magic-alt/qlib-platform/blob/main/CONTRIBUTING.md) | [Testing & Certification](testing_and_certification.md) |
| Find a first contribution | [Good First Issue Policy](project/good-first-issues.md) | [CONTRIBUTING](https://github.com/magic-alt/qlib-platform/blob/main/CONTRIBUTING.md) |
| Plan project work | [Roadmap](project/roadmap.md) | [Current Governance State](current_state.md) |
| Maintain GitHub/security automation | [Repository Governance](maintainers/repository-governance.md) | [Repository Settings](maintainers/repository-settings.md) |
| Cut a software release | [Release Process](maintainers/releasing.md) | [CHANGELOG](https://github.com/magic-alt/qlib-platform/blob/main/CHANGELOG.md) |
| Work on branding/docs UX | [Brand Guide](maintainers/branding.md) | [`mkdocs.yml`](https://github.com/magic-alt/qlib-platform/blob/main/mkdocs.yml) |

## Documentation authority

Not every Markdown file has the same authority.

| Document type | Meaning |
| --- | --- |
| **Active** | Current behavior or operating guidance; update when behavior changes. |
| **Normative contract** | Identity, boundary, schema, or invariant that implementation must preserve. |
| **Frozen certification / acceptance** | Evidence bound to a historical baseline; do not rewrite to match current `main`. |
| **History / moved** | Provenance or superseded material; not current operating guidance. |

When frozen evidence differs from current behavior, use [Current Governance State](current_state.md) plus active architecture/CLI/operations documents rather than editing historical evidence.

## Architecture and contracts

- **[Architecture Overview](architecture.md)** — system layers, deployment modes, identity flow, and failure model.
- **[Architecture Boundary](architecture_boundary.md)** — Research Plane / Execution Plane ownership.
- **[Identity and Lineage](identity_and_lineage.md)** — immutable release, dataset, snapshot, model, and artifact identities.
- **[Artifact Contract v2](artifact_contract_v2.md)** — governed cross-repository handoff.
- **[Configuration](configuration.md)** — profiles, environment variables, and dependency extras.
- **[CLI Reference](cli_reference.md)** — parser-aligned command surface and side-effect classification.
- **[Glossary](glossary.md)** — canonical terminology.

Recommended architecture reading order:

```text
Architecture
  -> Architecture Boundary
  -> Identity and Lineage
  -> Artifact Contract v2
  -> Configuration
  -> CLI Reference
```

## Data and research

- **[Qlib Data Platform](qlib_data_platform.md)** — data-release intake and Qlib materialization.
- **[Data Schema](data_schema.md)** — research-side schema expectations.
- **[Research Lifecycle](research_lifecycle.md)** — governed experiment stages and evidence flow.
- **[Active Phase 3-D](alpha_research_phase_3.md)** — active diagnostics protocol; always cross-check [Current State](current_state.md).
- **[Portfolio Policy](portfolio_v2_rank_buffer.md)** — typed portfolio-construction behavior.
- **[Standalone Sovereignty](standalone_sovereignty.md)** — standalone guarantees.
- **[Production ML Phase 4](production_ml_phase4.md)**, **[Model Lifecycle](model_lifecycle.md)**, and **[Production Feedback](production_feedback.md)** — local model/signal infrastructure and monitoring boundaries.

Core research identity flow:

```text
DataRelease
  -> DatasetVersion
  -> FeatureSnapshot
  -> Research / Walk-forward
  -> PredictionSnapshot / MODEL_RELEASE
  -> Research Backtest Evidence
  -> PortfolioPolicy
  -> TARGET_PORTFOLIO
  -> Artifact Contract v2
```

## Operations and validation

- **[Operations Runbook](OPERATIONS_RUNBOOK.md)** — primary operating entry point.
- **[Daily Research](operations/daily-research.md)** — routine research/signal workflow.
- **[Health & Observability](operations/health-and-observability.md)** — liveness, readiness, probes, and operational state.
- **[Outbox Delivery](operations/outbox.md)** — durable handoff semantics.
- **[Recovery](operations/recovery.md)** — safe retry/recovery procedures.
- **[Incident Response](operations/incident-response.md)** — incident classification and handling.
- **[Testing & Certification](testing_and_certification.md)** — validation layers and certification scope.
- **[Troubleshooting](troubleshooting.md)** — common failures and recovery guidance.
- **[TuShare Daily Sync](daily_sync.md)** and **[Windows LightGBM GPU](windows_lightgbm_gpu.md)** — environment-specific operational guidance.

## Project and community

- **[Roadmap & Milestones](project/roadmap.md)** — engineering direction and milestone exit criteria.
- **[Good First Issue Policy](project/good-first-issues.md)** — what qualifies as a safe newcomer task.
- **[CONTRIBUTING.md](https://github.com/magic-alt/qlib-platform/blob/main/CONTRIBUTING.md)** — contributor workflow, risk classification, review, and validation.
- **[Code of Conduct](https://github.com/magic-alt/qlib-platform/blob/main/CODE_OF_CONDUCT.md)** — professional conduct and research-integrity expectations.
- **[SECURITY.md](https://github.com/magic-alt/qlib-platform/blob/main/SECURITY.md)** — private handling of vulnerabilities and secret exposure.

## Maintainers

- **[Repository Governance](maintainers/repository-governance.md)** — Rulesets, required checks, Dependabot, CodeQL, Dependency Review, release provenance, SBOM, and Pages policy.
- **[Release Process](maintainers/releasing.md)** — version preparation, immutable tagging, automated release artifacts, verification, and fix-forward policy.
- **[Repository Settings](maintainers/repository-settings.md)** — About/Topics/Pages and settings stored outside Git.
- **[Brand Guide](maintainers/branding.md)** — logo, palette, typography, and diagram policy.
- **[CHANGELOG](https://github.com/magic-alt/qlib-platform/blob/main/CHANGELOG.md)** — user-visible software changes.

## Frozen evidence and history

- **[Research Infrastructure Certification](research_infrastructure_certification.md)** — certification bound to its explicit baseline.
- **[Full Walk-forward Acceptance](full_walk_forward_acceptance.md)** — frozen acceptance protocol.
- **[History Index](history/README.md)** — completed phases and superseded protocols.

QMT canonical execution ownership has moved to [`magic-alt/platform`](https://github.com/magic-alt/platform). The QMT pages retained here are migration/history pointers, not current Research Plane execution runbooks.

## Documentation contribution checklist

When changing documentation:

- keep CLI syntax in [CLI Reference](cli_reference.md) rather than duplicating command catalogs on high-level pages;
- distinguish `DataRelease`, `DatasetVersion`, snapshots, and target portfolios precisely;
- link moving research facts to [Current State](current_state.md);
- do not rewrite frozen evidence to describe current `main`;
- keep broker orders, fills, positions, ledger, and hard risk on the Execution Plane side;
- run `scripts/check_docs.py --root .`;
- run `python -m mkdocs build --strict` for site/navigation changes.
