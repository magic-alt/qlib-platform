---
status: ACTIVE
owner: architecture
applies_to_commit: 4f3f4369b6e55186967bc726bb8dd87fff0e5d70
last_verified: 2026-08-31
---

# Documentation Index

从 [Current State](current_state.md) 开始。它记录当前 reviewed/certified baseline、active research program、holdout/publishing 状态以及本轮文档审计基线；README 和冻结协议不保存这些会变化的 current-main 事实。

## Architecture and contracts

- [Architecture Overview](architecture.md) — 系统层次、数据流、standalone/integrated 模式与 failure model。
- [Architecture Boundary](architecture_boundary.md) — Research Plane / Execution Plane 的规范所有权边界。
- [Identity and Lineage](identity_and_lineage.md) — DataRelease、DatasetVersion、snapshots、feedback 与 Artifact v2 lineage。
- [Artifact Contract v2](artifact_contract_v2.md) — 唯一跨仓研究发布 contract 与 outbox delivery 生命周期。
- [Configuration](configuration.md) — profiles、环境变量、extras 与配置选择规则。
- [CLI Reference](cli_reference.md) — 当前 parser 对齐的命令语法、副作用和关键参数。
- [Glossary](glossary.md) — 统一术语。

## Data and research

- [Qlib Data Platform](qlib_data_platform.md)
- [Data Schema](data_schema.md)
- [Research Lifecycle](research_lifecycle.md)
- [Active Phase 3-D](alpha_research_phase_3.md)
- [Portfolio Policy Layers](portfolio_v2_rank_buffer.md)
- [Standalone Sovereignty](standalone_sovereignty.md)
- [Local qrun Example](../examples/local_qlib_backtest/README.md)

## Production ML and local signal operations

- [Production ML Phase 4](production_ml_phase4.md) — 当前 production-ML infrastructure roadmap。
- [Production Feedback](production_feedback.md) — RealizedLabelSnapshot / PredictionEvaluationSnapshot 的实际 CLI 和治理边界。
- [Model Lifecycle](model_lifecycle.md) — research result → local refit → local deployment → live signal。
- [Daily Research](operations/daily-research.md) — 单日 signal/research 值班流程。
- [TuShare Daily Sync](daily_sync.md) — 数据日更与 scheduler。
- [Windows LightGBM GPU](windows_lightgbm_gpu.md) — Windows OpenCL 构建/探测。

## Operations and validation

- [Operations Runbook](OPERATIONS_RUNBOOK.md) — 主运维入口与准确 CLI 示例。
- [Health and Observability](operations/health-and-observability.md)
- [Outbox Delivery](operations/outbox.md)
- [Recovery](operations/recovery.md)
- [Incident Response](operations/incident-response.md)
- [Testing and Certification](testing_and_certification.md)
- [Troubleshooting](troubleshooting.md)

## Certification

- [Research Infrastructure Certification](research_infrastructure_certification.md) — 冻结于 certified baseline 的认证声明。
- [Full Walk-forward Acceptance](full_walk_forward_acceptance.md) — 冻结 acceptance protocol。

冻结文档只描述其绑定的认证基线；当前 main/revalidation 状态统一查看 [Current State](current_state.md)。

## History and moved documentation

已完成研究阶段、旧 qrun 与 P0 说明只从 [History Index](history/README.md) 进入。历史文档不能作为当前命令或 handoff contract。

QMT canonical implementation 与 runbook 已迁移到 `magic-alt/platform`；本仓仅保留 [QMT moved pointer](qmt_gateway.md)。`QMT_极速策略交易系统_使用教程与API接口说明.md` 也标记为 `MOVED`，仅作为迁移来源，不是 Research Plane 的 active runbook。
