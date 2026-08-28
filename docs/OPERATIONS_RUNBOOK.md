---
status: ACTIVE
owner: operations
applies_to_commit: 8692afefe1f6cc82ab1f276fca788888f9f30f3e
last_verified: 2026-08-28
---

# Operations Runbook

本页是 Research Plane 运维入口。Broker/QMT、hard risk、LEAN validation、Paper、OMS、订单、
成交、对账、ledger recovery、kill switch 和 Production rollback 属于 `magic-alt/platform`。

## 命令基线

从仓库根目录运行：

```powershell
$RepoPython = '.\.venv\Scripts\python.exe'
```

```bash
RepoPython=.venv/bin/python
```

默认 CLI profile 是 `configs/pipeline.standalone.yaml`。只有 integrated workflow 才显式使用
`configs/pipeline.integrated.yaml`。不要把 `configs/pipeline.yaml` 当作通用默认配置。

## 1. 启动与健康检查

```powershell
& $RepoPython -m tushare_qlib status
& $RepoPython -m tushare_qlib health live
& $RepoPython -m tushare_qlib health ready
& $RepoPython -m tushare_qlib health dependencies
```

`health live` 只证明进程可响应；`ready` 与 `dependencies` 才检查本地配置、数据与可选依赖。

## 2. DataRelease：解析并独立验证

DataRelease 是不可变上游事实 release。它不是 DatasetVersion。

```powershell
& $RepoPython -m tushare_qlib --config configs/pipeline.integrated.yaml release verify `
  <DATA_RELEASE_REF>
```

该命令验证 DataRelease manifest identity、component identity、required roles/schema、文件
SHA-256 与 size。任何缺失或漂移都必须 fail closed。

Standalone release 可用 `release list` 查看。发布、导入、构建或 promotion 都是状态变更；
仅在用户明确授权精确来源、日期窗口与输出后运行 `release import-qlib`、
`release build-local`、`release build-tushare` 或 `release promote`。

## 3. DatasetVersion：物化、解析并验证

DataRelease 经 Qlib materialization 产生绑定的 DatasetVersion。研究和 inference 使用
DatasetVersion ID/alias：

```powershell
& $RepoPython -m tushare_qlib --config configs/pipeline.integrated.yaml dataset-resolve `
  <DATASET_VERSION_REF>
& $RepoPython -m tushare_qlib --config configs/pipeline.integrated.yaml dataset-show `
  <DATASET_VERSION_REF>
& $RepoPython -m tushare_qlib --config configs/pipeline.integrated.yaml dataset-verify `
  <DATASET_VERSION_REF>
```

`dataset-verify` 重算 DatasetVersion identity 并校验 partitions；它不替代前一步的
DataRelease verification。确认 DatasetVersion manifest 中的 DataRelease binding 与预期 release
一致后，才进入 research/inference。Alias promotion 是状态变更。

## 4. Model lifecycle

```powershell
& $RepoPython -m tushare_qlib model-status
```

`model-refit` 创建新的本地 ModelRelease；`model-deploy` 与 `model-rollback` 改变本地部署选择。
运行前必须确认 DatasetVersion reference、训练窗口、deployment ID 和输出目录。它们不改变
`platform` 的 deployment state。详见 [Model Lifecycle](model_lifecycle.md)。

## 5. Live inference 与 daily signal

```powershell
& $RepoPython -m tushare_qlib live-inference `
  --as-of <YYYY-MM-DD> `
  --dataset-ref <DATASET_VERSION_REF> `
  --deployment-id <LOCAL_MODEL_RELEASE_ID>
```

`--dataset-ref` 只能是 DatasetVersion ID/alias。先验证 `as-of`、DataRelease binding、signal date、
trade date、model release 与 feature lineage。任何 identity/checksum/date mismatch 均停止，不尝试修补。

`daily-signal-run` 是状态变更：会写 research artifacts/outbox。运行前确认日期、dataset reference、
deployment、输出与 delivery endpoint。

## 6. Artifact outbox

```powershell
& $RepoPython -m tushare_qlib outbox drain --endpoint <PLATFORM_ENDPOINT>
& $RepoPython -m tushare_qlib outbox worker --endpoint <PLATFORM_ENDPOINT> --once
```

平台不可用时，本地研究继续；已验证 Artifact v2 bundle 留在 durable outbox。只有成功的 2xx ACK
确认 delivery。不得通过删除队列或改写 `externalRunId` 处理重试。

## 7. 生产状态查询与恢复

```powershell
& $RepoPython -m tushare_qlib ops-query
& $RepoPython -m tushare_qlib ops-summary
& $RepoPython -m tushare_qlib ops-retry-delivery <RUN_ID>
& $RepoPython -m tushare_qlib ops-ack <RUN_ID>
```

`ops-query` 和 `ops-summary` 是读取入口；retry/ack 会改变本地 delivery state。恢复流程见
[Recovery](operations/recovery.md)，事件处置见 [Incident Response](operations/incident-response.md)。

## 8. Auth 与 bootstrap

`auth user-list` 是读取操作；`auth bootstrap-admin`、`auth user-create` 与 `bootstrap`
会写本地状态。不得在命令、日志或文档中显示 credential 值；只记录变量名和脱敏状态。

## 9. 禁止在本仓库执行的工作

- broker/QMT order submit/cancel/replace；
- broker positions、fills、ledger 或 authoritative account state 写入；
- hard-risk enforcement、Production kill switch 或 LEAN authoritative execution；
- 将 `MODEL_TOPK`、模拟 order 或研究 audit 当作跨仓执行接口。

本仓库的唯一执行语义 handoff 是 Artifact Contract v2 中绑定单一 DataRelease 的
`TARGET_PORTFOLIO`。
