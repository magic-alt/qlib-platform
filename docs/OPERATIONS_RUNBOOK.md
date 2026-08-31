---
status: ACTIVE
owner: operations
applies_to_commit: 4f3f4369b6e55186967bc726bb8dd87fff0e5d70
last_verified: 2026-08-31
---

# Operations Runbook

本页是 Research Plane 的主运维入口。Broker/QMT、hard risk、authoritative LEAN validation、Paper/Shadow、OMS、订单、成交、持仓、对账、ledger recovery、kill switch 与 Production rollback 属于 `magic-alt/platform`。

## 0. 运行原则

1. 从仓库根目录使用 repository-local Python；
2. 先做 read-only / verification-first 检查，再执行写操作；
3. DataRelease 与 DatasetVersion 必须分别验证，不把二者 ID 混用；
4. 对所有状态变更明确日期、reference、alias、deployment、endpoint 与 output；
5. 失败时保留原始 manifest/checksum/log/state，禁止通过修改不可变 artifact “修好”校验；
6. 当前研究阶段的 holdout/publishing 限制优先于通用 CLI 能力。

Windows：

```powershell
$RepoPython = '.\.venv\Scripts\python.exe'
```

Linux/macOS：

```bash
RepoPython=.venv/bin/python
```

默认 profile 是 `configs/pipeline.standalone.yaml`。Integrated workflow 显式使用 `configs/pipeline.integrated.yaml`。不要把 `configs/pipeline.yaml` 当作通用默认配置。

## 1. 启动与健康检查

```powershell
& $RepoPython -m tushare_qlib status
& $RepoPython -m tushare_qlib health live
& $RepoPython -m tushare_qlib health ready
& $RepoPython -m tushare_qlib health dependencies
```

判读：

- `health live`：仅进程可响应；
- `health ready`：本机配置、文件系统、Registry 等是否可安全工作；
- `health dependencies`：数据、TuShare、platform/adapter 等依赖状态；
- platform 不可用可以是 dependency degraded，但 identity/checksum/本地文件系统错误不能降级为“仅外部服务不可用”。

详见 [Health and Observability](operations/health-and-observability.md)。

## 2. DataRelease：先验证上游事实

Integrated 示例：

```powershell
& $RepoPython -m tushare_qlib --config configs/pipeline.integrated.yaml release verify `
  <DATA_RELEASE_REF> --mode deep
```

DataRelease 是不可变上游事实 release。`release verify` 校验 manifest identity、component identity、required roles/schema 和文件 payload。

常用只读入口：

```powershell
& $RepoPython -m tushare_qlib release list
```

下列命令会改变本地发布状态，必须显式确认目标：

```text
release import-qlib --path <PATH>
release build-local [--start DATE --end DATE]
release build-tushare --start DATE --end DATE
release promote <REF> --alias <ALIAS>
```

## 3. DatasetVersion：物化后独立验证

研究/inference 使用 DatasetVersion ID/alias：

```powershell
& $RepoPython -m tushare_qlib --config configs/pipeline.integrated.yaml dataset-resolve `
  <DATASET_VERSION_REF>
& $RepoPython -m tushare_qlib --config configs/pipeline.integrated.yaml dataset-show `
  <DATASET_VERSION_REF>
& $RepoPython -m tushare_qlib --config configs/pipeline.integrated.yaml dataset-verify `
  <DATASET_VERSION_REF> --mode deep
```

`dataset-verify` 重算 DatasetVersion identity 并验证 partitions；它不替代 DataRelease verification。确认 DatasetVersion manifest 的 DataRelease binding 与预期一致后再进入研究或 inference。

`dataset-promote` / `registry-rebuild` / `dataset-build` 都是状态变更。验证等级与布局见 [Qlib Data Platform](qlib_data_platform.md)。

## 4. Research / model 前置检查

当前研究治理状态先看 [Current State](current_state.md)。不要仅因为通用 CLI 存在，就绕过 active program 的 candidate/holdout/publishing 限制。

本地模型状态：

```powershell
& $RepoPython -m tushare_qlib model-status
```

Refit / deployment：

```text
model-refit --research-run <PROMOTED_WALK_FORWARD_RUN> --as-of <YYYY-MM-DD>
model-deploy <DEPLOYMENT_ID> [--device cpu]
model-rollback --to <DEPLOYMENT_ID> [--device cpu]
```

`model-refit` 使用配置中 pin 的 DatasetVersion；它没有 `--dataset-ref` 参数。它要求研究 release 已经 `PROMOTED`、decision 为 `PROMOTE` 且 lineage 完整。

详见 [Model Lifecycle](model_lifecycle.md)。

## 5. Live inference

推荐形式：

```powershell
& $RepoPython -m tushare_qlib live-inference `
  --as-of <YYYY-MM-DD> `
  --dataset-ref <DATASET_VERSION_REF> `
  --deployment-id <LOCAL_DEPLOYMENT_ID>
```

该命令是写操作：会生成 local live-signal artifact 并写 signal registry state。它通常产生 score、TopK、health、attestation 与 manifest。

运行前确认：

- DatasetVersion 已验证且绑定预期 DataRelease；
- deployment 本地状态为 `DEPLOYED`；
- `as-of` 是预期 signal date；
- bundle/feature schema/parity 没有漂移；
- 如果要求同步完成，使用 `--require-daily-sync`。

任何 identity/checksum/schema/date/health mismatch 都应停止，不要修补 manifest。

## 6. Daily signal runner

```powershell
& $RepoPython -m tushare_qlib daily-signal-run --as-of <YYYY-MM-DD>
```

默认行为是：

```text
create local pipeline run
    -> daily-sync
    -> live-inference using configured dataset + current local deployment
    -> persist local signal/health/ops state
    -> optional Feishu notification
```

可选：

- `--skip-sync`：跳过当天数据同步；
- `--no-notify`：不发送 Feishu；
- `--supersede`：显式允许本地 signal registry 的 supersede 语义。

**重要：`daily-signal-run` 不会自动执行 `artifact-v2-export`，也不会自动 drain Artifact outbox。** 研究 artifact 的跨仓导出与 delivery 是单独操作。

详见 [Daily Research](operations/daily-research.md)。

## 7. Production feedback evidence

反馈命令创建 monitoring evidence，不执行 selection/promotion/deployment：

```text
feedback-build-labels --labels <PARQUET> --calendar <FILE> --observed-through <DATE>
  --data-release-id <ID> --label-spec-id <ID> --horizon-days <N> --signal-lag-days <N>
  --source-artifact-id <ID> --output <OUTPUT>

feedback-evaluate --predictions <PREDICTION_SNAPSHOT>
  --realized-labels <REALIZED_LABEL_SNAPSHOT> --output <OUTPUT>
```

`feedback-evaluate` 在 evaluation decision 非 `PASS` 时退出非零。完整边界见 [Production Feedback](production_feedback.md)。

## 8. Artifact v2 export 与 outbox

Export：

```text
artifact-v2-export <RESEARCH_MANIFEST>
  --output-dir <DIR>
  --git-commit <SHA>
  --container-digest <DIGEST>
  [--data-release-id <ID>]
```

Export 成功会将 verified bundle 加入本地 durable outbox，但不会等待远端 endpoint。

Delivery：

```powershell
& $RepoPython -m tushare_qlib outbox drain --endpoint <PLATFORM_ENDPOINT>
& $RepoPython -m tushare_qlib outbox worker --endpoint <PLATFORM_ENDPOINT> --once
```

`PLATFORM_ARTIFACT_ENDPOINT` 可以替代 `--endpoint`。只有 2xx acknowledgement 才标记成功。重试同一个 immutable payload；禁止更改 DataRelease、parents、checksum、`externalRunId` 或 idempotency identity。

详见 [Outbox Delivery](operations/outbox.md)。

## 9. Ops 查询、恢复与 acknowledgement

当前 CLI 的准确语法是：

```powershell
& $RepoPython -m tushare_qlib ops-query --entity runs --business-date <YYYY-MM-DD>
& $RepoPython -m tushare_qlib ops-query --entity deliveries --status <STATUS>
& $RepoPython -m tushare_qlib ops-summary --business-date <YYYY-MM-DD>
& $RepoPython -m tushare_qlib ops-retry-delivery <IDEMPOTENCY_KEY>
& $RepoPython -m tushare_qlib ops-ack `
  --entity delivery --id <ID> --operator <OPERATOR> --reason <REASON>
```

注意：

- `ops-query` 必须指定 `--entity runs|deliveries`；
- `ops-summary` 必须指定 `--business-date`；
- `ops-retry-delivery` 的位置参数是 delivery `idempotency_key`，不是任意 `RUN_ID`；
- `ops-ack` 需要 `--entity`, `--id`, `--operator`, `--reason`，不是 `ops-ack <RUN_ID>`；
- acknowledgement 是可审计的人工状态记录，不是修复根因的替代品。

恢复见 [Recovery](operations/recovery.md)，事件处置见 [Incident Response](operations/incident-response.md)。

## 10. Auth 与 bootstrap

`auth user-list` 是读取操作。`auth bootstrap-admin`、`auth user-create` 与 `bootstrap` 会写本地状态。

密码通过交互输入，不应作为 CLI 参数。日志/文档/截图只记录变量名或脱敏状态，禁止输出 credential value。

## 11. 禁止在本仓库执行的工作

- broker/QMT order submit/cancel/replace；
- broker positions、fills、ledger 或 authoritative account state 写入；
- hard-risk enforcement、Production kill switch 或 authoritative LEAN execution；
- 将 `MODEL_TOPK`、模拟 Qlib fill/order 或 research audit 当作跨仓执行接口；
- 通过打开 final holdout、发布模型或改变研究门槛来排障。

本仓库唯一具有 execution semantics 的跨仓 handoff 是 Artifact Contract v2 中绑定单一 DataRelease 的 `TARGET_PORTFOLIO`。
