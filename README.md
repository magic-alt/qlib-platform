# Qlib Research / Alpha Factory

`qlib-platform` 是独立的 Research Plane。它消费或发布不可变 `DataRelease`，完成特征、
walk-forward、模型与研究组合工作，并通过 Artifact Contract v2 交付研究 artifact。
`platform` 是可选的 Execution Plane，负责权威 LEAN 语义、hard risk、OMS、QMT/券商、
订单、成交与 ledger。

跨仓库唯一具有执行语义的 handoff 是：绑定且仅绑定一个不可变 `DataRelease` 的
`TARGET_PORTFOLIO`。本仓库不提交、撤销或替换 broker order，也不写 broker state。

## 当前治理状态

- Reviewed code baseline: `8692afefe1f6cc82ab1f276fca788888f9f30f3e`（2026-08-26）。
- Certified infrastructure baseline: `4f5c5d5`（2026-08-17）。
- Post-baseline status: `INCREMENTAL_REVALIDATION_REQUIRED`；当前 main 不作为整体自动继承
  `4f5c5d5` 的全部认证声明。
- Active research program: Phase 3-D / `ashare_alpha_stability_phase3_v1`。
- Phase 3-D: diagnostics only；formal candidates、model selection、final-holdout access 与
  publishing 均禁用。
- Artifact contract: v2；本仓库最多推进到 `RESEARCH_PROMOTED`。

当前事实的唯一入口是 [Current State](docs/current_state.md)，文档导航见
[Documentation Index](docs/index.md)。

## 安装与解释器

从仓库根目录创建环境。所有本地命令必须使用仓库内解释器：

```powershell
python3.12 -m venv .venv
$RepoPython = '.\.venv\Scripts\python.exe'
& $RepoPython -m pip install -c constraints/ci.txt -e ".[dev]"
```

```bash
python3.12 -m venv .venv
RepoPython=.venv/bin/python
$RepoPython -m pip install -c constraints/ci.txt -e '.[dev]'
```

需要 operational data、Qlib 或 PyTorch 时，按 [Configuration](docs/configuration.md) 选择 extras。
不要使用系统 Python、全局 `tq`、全局 `qrun` 或本地 Makefile target。

## 安全起步

CLI 默认读取 `configs/pipeline.standalone.yaml`，不要求 `platform` 或 TuShare 才能启动：

```powershell
& $RepoPython -m tushare_qlib status
& $RepoPython -m tushare_qlib health dependencies
& $RepoPython -m tushare_qlib release list
```

Integrated research 必须显式选择 `configs/pipeline.integrated.yaml`，并绑定外部发布的
`DataRelease`。配置 profile 的用途和 capability 见
[Configuration](docs/configuration.md)。

## 不可变身份的正确顺序

```text
DataRelease
    -> materialize
DatasetVersion
    -> FeatureSnapshot
    -> PredictionSnapshot / MODEL_RELEASE
    -> PortfolioPolicy
    -> TARGET_PORTFOLIO
    -> Artifact Contract v2
    -> platform
```

`release verify` 验证 DataRelease；`dataset-verify` 验证 DatasetVersion/reference。
`live-inference --dataset-ref` 接受 DatasetVersion ID 或 alias，不接受 DataRelease ID。
完整定义见 [Identity and Lineage](docs/identity_and_lineage.md) 和
[Operations Runbook](docs/OPERATIONS_RUNBOOK.md)。

## 研究与 qrun

- 正式 governed research：使用 `research-run`、明确 config/profile，并固定不可变输入。
- 本地 qrun 教程：只使用 [examples/local_qlib_backtest](examples/local_qlib_backtest/README.md)。
- `QLIB_DATA_URI` 必须来自 `dataset-resolve` 的不可变 DatasetVersion 路径；workflow 不得写死
  workstation path。
- Phase 3-D 命令与禁止事项见 [Alpha Research Phase 3](docs/alpha_research_phase_3.md)。

## 常用只读验证

```powershell
& $RepoPython scripts/check_docs.py --root .
& $RepoPython -m ruff check src tests
& $RepoPython -m ruff format --check src tests
& $RepoPython -m mypy src
& $RepoPython -m tushare_qlib --config configs/pipeline.integrated.yaml validate-qrun-contract
& $RepoPython -m pytest
```

`backfill`、`stage-*`、`dump-*`、`daily-sync`、`release build-*`、
`dataset-promote`、`model-deploy`、`model-rollback`、`phase3-diagnose` 和 scheduled-task
安装/移除都是状态变更，必须先确认精确目标与输出。

## 关键文档

- [Architecture Boundary](docs/architecture_boundary.md)
- [Configuration](docs/configuration.md)
- [CLI Reference](docs/cli_reference.md)
- [Artifact Contract v2](docs/artifact_contract_v2.md)
- [Research Lifecycle](docs/research_lifecycle.md)
- [Testing and Certification](docs/testing_and_certification.md)
- [Operations Runbook](docs/OPERATIONS_RUNBOOK.md)
- [Troubleshooting](docs/troubleshooting.md)

历史协议、已完成研究阶段和已废弃教程只从
[History Index](docs/history/README.md) 进入，不作为当前操作入口。
