---
status: ACTIVE
owner: architecture
applies_to_commit: 8692afefe1f6cc82ab1f276fca788888f9f30f3e
last_verified: 2026-08-28
---

# Documentation Index

从 [Current State](current_state.md) 开始。它记录当前 code baseline、认证边界、active research program、
holdout 与 publishing 状态；README 不再承担历史状态存储。

## Architecture and contracts

- [Architecture Overview](architecture.md)
- [Architecture Boundary](architecture_boundary.md)
- [Identity and Lineage](identity_and_lineage.md)
- [Artifact Contract v2](artifact_contract_v2.md)
- [Production ML Phase 4](production_ml_phase4.md)
- [Configuration](configuration.md)
- [CLI Reference](cli_reference.md)
- [Glossary](glossary.md)

## Data and research

- [Qlib Data Platform](qlib_data_platform.md)
- [Data Schema](data_schema.md)
- [Research Lifecycle](research_lifecycle.md)
- [Active Phase 3-D](alpha_research_phase_3.md)
- [Portfolio Policy Layers](portfolio_v2_rank_buffer.md)
- [Standalone Sovereignty](standalone_sovereignty.md)
- [Local qrun Example](../examples/local_qlib_backtest/README.md)

## Operations and validation

- [Operations Runbook](OPERATIONS_RUNBOOK.md)
- [Model Lifecycle](model_lifecycle.md)
- [Daily Research](operations/daily-research.md)
- [Health and Observability](operations/health-and-observability.md)
- [Outbox Delivery](operations/outbox.md)
- [Recovery](operations/recovery.md)
- [Incident Response](operations/incident-response.md)
- [Testing and Certification](testing_and_certification.md)
- [Troubleshooting](troubleshooting.md)

## History

已完成研究阶段、冻结认证协议、旧 qrun 与 P0 说明只从
[History Index](history/README.md) 进入。历史文档不能作为当前命令或 handoff contract。

QMT 的 canonical implementation 与 runbook 已迁移到 `magic-alt/platform`；本仓只保留
[QMT moved pointer](qmt_gateway.md)。
