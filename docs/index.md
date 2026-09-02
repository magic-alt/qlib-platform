---
status: ACTIVE
owner: architecture
applies_to_commit: 85bac85356d8092adfe98cd82ee59f81a242cf53
last_verified: 2026-09-02
---

# qlib-platform Documentation

![qlib-platform](assets/brand/qlib-platform-logo.svg)

This is the canonical navigation page for `qlib-platform` documentation.

If you are new to the repository, do **not** start by reading every design document. Use the short onboarding path below, then branch into the area that matches your task.

> [!IMPORTANT]
> Fast-changing governance facts live only in [Current State](current_state.md). README pages, frozen certification documents and historical protocols must not duplicate moving values such as the active research phase, reviewed SHA, holdout authorization or publishing state.

## 5-minute onboarding path

1. **[README](../README.md)** — understand what the project is, what it owns and where research stops.
2. **[Architecture](architecture.md)** — learn the Research Plane layers, identity flow and failure model.
3. **[Local Qlib Backtest Example](../examples/local_qlib_backtest/README.md)** — run the smallest maintained Qlib workflow.
4. **[CLI Reference](cli_reference.md)** — map commands to configuration, inputs, outputs and side effects.
5. **[Current State](current_state.md)** — check the active governance state before governed research or publication.

After these five pages, most contributors can navigate the repository without reading the documentation linearly.

---

## Choose your task

| I want to... | Start with | Then read |
| --- | --- | --- |
| Understand the system | [Architecture](architecture.md) | [Architecture Boundary](architecture_boundary.md), [Identity and Lineage](identity_and_lineage.md) |
| Install and configure locally | [README Quick Start](../README.md#quick-start) | [Configuration](configuration.md), [Troubleshooting](troubleshooting.md) |
| Run Qlib research | [Local Qlib Backtest Example](../examples/local_qlib_backtest/README.md) | [Research Lifecycle](research_lifecycle.md), [CLI Reference](cli_reference.md) |
| Work with data releases / datasets | [Qlib Data Platform](qlib_data_platform.md) | [Data Schema](data_schema.md), [Identity and Lineage](identity_and_lineage.md) |
| Develop features / alphas / diagnostics | [Research Lifecycle](research_lifecycle.md) | [Active Phase 3-D](alpha_research_phase_3.md), [Portfolio Policy Layers](portfolio_v2_rank_buffer.md) |
| Operate daily research jobs | [Operations Runbook](OPERATIONS_RUNBOOK.md) | [Daily Research](operations/daily-research.md), [Health and Observability](operations/health-and-observability.md) |
| Sync TuShare data | [TuShare Daily Sync](daily_sync.md) | [Configuration](configuration.md), [Recovery](operations/recovery.md) |
| Integrate with `magic-alt/platform` | [Architecture Boundary](architecture_boundary.md) | [Artifact Contract v2](artifact_contract_v2.md), [Identity and Lineage](identity_and_lineage.md) |
| Work on local model deployment / signals | [Model Lifecycle](model_lifecycle.md) | [Production Feedback](production_feedback.md), [Current State](current_state.md) |
| Debug a failure | [Troubleshooting](troubleshooting.md) | [Operations Runbook](OPERATIONS_RUNBOOK.md), [Recovery](operations/recovery.md) |
| Validate a change | [Testing and Certification](testing_and_certification.md) | [CONTRIBUTING](../CONTRIBUTING.md), [Current State](current_state.md) |
| Cut a software release | [Release Process](maintainers/releasing.md) | [CHANGELOG](../CHANGELOG.md), [Repository Settings](maintainers/repository-settings.md) |
| Work on public branding/docs UX | [Brand Guide](maintainers/branding.md) | [Repository Settings](maintainers/repository-settings.md), [`mkdocs.yml`](../mkdocs.yml) |

---

## Documentation model

Not every Markdown file has the same authority. Read its status and purpose before using it as an operating instruction.

| Document type | Meaning | How to use it |
| --- | --- | --- |
| **Active** | Describes current repository behavior or operating guidance | Use for current development and operations; update when behavior changes |
| **Normative contract** | Defines an identity, boundary, schema or invariant | Treat as part of the implementation contract; changes require compatibility review |
| **Frozen certification / acceptance** | Bound to an explicit historical code/research baseline | Do not rewrite it to match current main; use it only for the baseline it certifies |
| **History / moved** | Retained for provenance, migration or completed research phases | Never treat as current CLI or operating guidance |

When a frozen document and current behavior differ, consult [Current State](current_state.md) and the active architecture/CLI/operations documents rather than silently editing the frozen record.

---

## Architecture and contracts

These documents define the stable mental model of the system.

- **[Architecture Overview](architecture.md)** — system layers, identity/data flow, standalone/integrated modes and failure model.
- **[Architecture Boundary](architecture_boundary.md)** — normative ownership boundary between `qlib-platform` Research Plane and `magic-alt/platform` Execution Plane.
- **[Identity and Lineage](identity_and_lineage.md)** — `DataRelease`, `DatasetVersion`, snapshots, model/research artifacts, feedback and parent binding.
- **[Artifact Contract v2](artifact_contract_v2.md)** — cross-repository research publication contract and durable delivery lifecycle.
- **[Configuration](configuration.md)** — profiles, environment variables, optional dependency extras and configuration-selection rules.
- **[CLI Reference](cli_reference.md)** — current parser-aligned command syntax, side effects and key parameters.
- **[Glossary](glossary.md)** — canonical terminology.

### Recommended reading order for architecture work

```text
Architecture
  -> Architecture Boundary
  -> Identity and Lineage
  -> Artifact Contract v2
  -> Configuration
  -> CLI Reference
```

---

## Data and research

- **[Qlib Data Platform](qlib_data_platform.md)** — research data entry, Qlib materialization and data lifecycle.
- **[Data Schema](data_schema.md)** — research-side data representation and schema expectations.
- **[Research Lifecycle](research_lifecycle.md)** — governed experiment stages and research evidence flow.
- **[Active Phase 3-D](alpha_research_phase_3.md)** — active diagnostic protocol; always cross-check [Current State](current_state.md).
- **[Portfolio Policy Layers](portfolio_v2_rank_buffer.md)** — typed portfolio construction and rank-buffer/top-k policy behavior.
- **[Standalone Sovereignty](standalone_sovereignty.md)** — guarantees and expectations for standalone operation.
- **[Local Qlib Backtest Example](../examples/local_qlib_backtest/README.md)** — maintained runnable qrun/Qlib example.

### Research identity flow

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

The identifiers in this chain are not interchangeable. In particular, a `DataRelease` and a `DatasetVersion` represent different immutable identities.

---

## Production ML and local signal operations

These pages describe local model lifecycle and signal-generation infrastructure. They do not transfer broker-state ownership into this repository.

- **[Production ML Phase 4](production_ml_phase4.md)** — production-ML infrastructure roadmap and constraints.
- **[Production Feedback](production_feedback.md)** — `RealizedLabelSnapshot` / `PredictionEvaluationSnapshot` CLI and governance boundary.
- **[Model Lifecycle](model_lifecycle.md)** — research result → local refit → local deployment → live signal.
- **[Daily Research](operations/daily-research.md)** — daily signal/research operating flow.
- **[TuShare Daily Sync](daily_sync.md)** — data refresh, publication and scheduler workflow.
- **[Windows LightGBM GPU](windows_lightgbm_gpu.md)** — Windows OpenCL build and runtime probe guidance.

Before model selection, holdout access, publication or promotion, check [Current State](current_state.md). Generic CLI availability is not authorization.

---

## Operations and validation

- **[Operations Runbook](OPERATIONS_RUNBOOK.md)** — primary operational entry point and command examples.
- **[Health and Observability](operations/health-and-observability.md)** — liveness/readiness, probes and operational state.
- **[Outbox Delivery](operations/outbox.md)** — durable artifact delivery semantics.
- **[Recovery](operations/recovery.md)** — safe retry and recovery procedures.
- **[Incident Response](operations/incident-response.md)** — incident classification and handling.
- **[Testing and Certification](testing_and_certification.md)** — validation layers, certification scope and evidence expectations.
- **[Troubleshooting](troubleshooting.md)** — common installation/runtime/research problems.

For code contributions, pair these with **[CONTRIBUTING.md](../CONTRIBUTING.md)** and the pull-request template.

---

## Maintainer and project operations

- **[Release Process](maintainers/releasing.md)** — software versioning, release checks, tag/release flow, and rollback policy.
- **[Repository Settings](maintainers/repository-settings.md)** — GitHub About/Topics, Pages, merge policy, and recommended ruleset settings that live outside tracked files.
- **[Brand Guide](maintainers/branding.md)** — logo assets, palette, typography, diagram policy, and external-use guidance.
- **[CHANGELOG](../CHANGELOG.md)** — user-visible software changes and version history.
- **[Code of Conduct](../CODE_OF_CONDUCT.md)** — collaboration and research-integrity expectations.

---

## Certification and frozen evidence

- **[Research Infrastructure Certification](research_infrastructure_certification.md)** — certification statement bound to its frozen certified baseline.
- **[Full Walk-forward Acceptance](full_walk_forward_acceptance.md)** — frozen acceptance protocol.

Frozen documents answer questions about the baseline they certify. They do not automatically certify post-baseline code changes.

For the latest relationship between documentation, reviewed code and certified baselines, use [Current State](current_state.md).

---

## History and moved documentation

Completed research phases, superseded qrun material and deprecated tutorials live under **[docs/history](history/README.md)**.

Historical material is preserved for lineage and auditability, not for current operations.

QMT canonical implementation and operational ownership have moved to [`magic-alt/platform`](https://github.com/magic-alt/platform). This repository keeps migration pointers such as [QMT Gateway](qmt_gateway.md); the legacy `QMT_极速策略交易系统_使用教程与API接口说明.md` is retained as moved source material rather than an active Research Plane runbook.

---

## Documentation contribution checklist

When changing documentation:

- verify repository-relative links;
- keep CLI examples aligned with the current parser;
- distinguish `DataRelease` from `DatasetVersion` and other research identities;
- link moving governance facts to [Current State](current_state.md) instead of copying them;
- do not rewrite frozen certification/history files to describe current main;
- keep execution-owned concepts (orders, fills, positions, ledger, hard risk) on the `platform` side of the boundary;
- run `scripts/check_docs.py --root .` before opening a pull request;
- run `python -m mkdocs build --strict` when changing the documentation site or navigation.

See [CONTRIBUTING.md](../CONTRIBUTING.md) for the complete contributor workflow.
