---
status: ACTIVE
owner: operations
applies_to_commit: 8692afefe1f6cc82ab1f276fca788888f9f30f3e
last_verified: 2026-08-31
---

# TuShare 每日数据同步

`daily-sync` 是面向 Tushare 数据源的幂等、状态变更发布命令。Windows 任务计划可在每日 18:30 调用它；节假日或没有内容变化时以 `noop` 正常结束。只在已授权的业务窗口运行它。

## 当前数据平台口径

- `data/bronze/tushare/current/` 是完整且唯一的本地 raw working view；变更分区会在此原子替换，不再生成平行的 `revisions/` 数据集。可复现性由发布时冻结的 `data/bronze/versions/` 和 immutable DataRelease 保证。
- `daily-sync` 的日常入口现在覆盖：基础日频数据、公司行为/复权历史、extended `market_reference` 日分区、最近财务报告期刷新、必要时的 PIT fundamentals 重建，以及后续 Qlib/DataRelease 发布。
- 每次成功发布都会冻结 Bronze、Silver、Gold 快照，并把父版本关系写入 Registry，随后发布不可变的 `data/qlib/versions/<version_id>/`。
- 默认 standalone profile 的 DatasetVersion alias 是 `standalone-current`；`research-release-current` 仍是 DataRelease alias。显式使用 `configs/pipeline_tushare_dev.yaml` 时，其 DatasetVersion alias 仍为 `research-current`。
- daily-sync 失败时不会替换当前已发布 alias；失败前已经进入发布链路的基础行情变更、历史修订或 PIT 变更由 `data/state/daily_sync/pending_publish.json` 记录以便下次恢复。
- 空的 legacy `data/bronze/tushare/current/extended/hsgt_moneyflow/` 目录会在日更时清理；正确的 TuShare endpoint/目录名称是 `moneyflow_hsgt`。
- 股票主数据和交易日历等元数据 working view 以原子替换写入，因此后续同步不会改写已发布版本硬链接的快照。
- Qlib close 使用首日归一化的稳定总回报序列；`export-kline` 可由本地原始数据生成 qfq/hfq 视图，不访问远端 API。

## 首次准备与检查

从仓库根目录使用本地解释器：

```powershell
$RepoPython = '.\.venv\Scripts\python.exe'
& $RepoPython -m qlib_platform sync-dividends --bootstrap --resume
& $RepoPython -m qlib_platform daily-sync --check-only
```

`--check-only` 只检查基础日频/公司行为数据源与质量，不写入正式 Bronze、extended、PIT 或 Qlib 版本。它不会执行 extended 财务刷新。首次全量准备仍使用 `bootstrap --source tushare` 或受控的 `backfill-extended`；首次全量发布或结构性修复使用 `dataset-build`。迁移既有布局时，先按 [`qlib_data_platform.md`](qlib_data_platform.md) 的 dry run 检查，再显式执行迁移。

## 日常一键发布

默认 standalone profile：

```powershell
& $RepoPython -m qlib_platform daily-sync --as-of <YYYY-MM-DD>
& $RepoPython -m qlib_platform dataset-verify standalone-current
& $RepoPython -m qlib_platform dataset-resolve standalone-current
```

如果显式使用 TuShare development profile：

```powershell
& $RepoPython -m qlib_platform --config configs/pipeline_tushare_dev.yaml daily-sync --as-of <YYYY-MM-DD>
& $RepoPython -m qlib_platform --config configs/pipeline_tushare_dev.yaml dataset-resolve research-current
```

一次成功的 `daily-sync` 按以下顺序执行：

1. 检查最近基础交易日，并在最近 `market_catchup_trading_days` 个交易日内补齐基础 raw 缺口；同时处理复权因子历史和增量股息。
2. 对完整基础 raw working view 做交易日历覆盖与分区深检；失败立即停止发布并保留真正存在的 pending work。
3. 对 7 个 `market_reference` extended endpoint 做全配置历史范围的 gap-fill：`limit_list_d`、`block_trade`、`top_list`、`margin`、`margin_detail`、`moneyflow_hsgt`、`hsgt_top10`。已经 terminal 的交易日分区不会重复请求，因此停跑后直接再次执行 `daily-sync` 即可补缺失交易日。
4. 对最近的财务报告期重新请求 financial extended endpoint。这里不会沿用“terminal 即永远跳过”的规则：已成功/empty 的最近季度会被重新检查，但只有逻辑内容真正变化时才原子替换分区；`permission_denied` 等不可用状态不会因为日更而被反复强刷。
5. 根据 `fina_indicator_vip` 各分区的逻辑内容指纹判断 PIT 源是否变化。源指纹变化时才重建 `gold/pit/current/fundamentals_daily.parquet`。即使上次运行在 extended 写入后、PIT 重建前异常退出，下次运行也能通过源指纹差异恢复。
6. PIT 逻辑内容变化时，把受影响证券加入 repair 集，并强制重建已有 curated 日分区，避免旧 curated/Qlib 继续引用旧 PIT 值。
7. 刷新参考元数据，构建 Qlib 候选版本；standalone 模式下发布新的 immutable DataRelease/DatasetVersion，并在所有验证通过后原子切换 `research-release-current` + `standalone-current`。

最近财务报告期默认回看 400 个自然日，可在配置中调整：

```yaml
data_sync:
  extended_financial_lookback_calendar_days: 400
```

这个窗口用于捕获半年报/年报等在报告期结束后继续出现的新增公告、补充披露和修订；它不是历史财务全量 backfill。需要首次历史抓取或主动重抓更老分区时，继续使用显式 `backfill-extended --start ... --end ...`。`--force` 是人工修复工具，不应作为正常日更参数。

基础发布前会按完整交易日历验证 `daily`、`adj_factor`、`daily_basic` 在 raw `current` 中无缺口，并对本次涉及分区深检 manifest、文件 SHA-256、行列数、必需字段、唯一键和分区日期；失败会 fail closed。同时会验证本次 changed trade dates 全部进入候选 Qlib 日历；存在中间缺口时不会切换当前 alias。追加交易日和历史修订都通过 copy-on-write 候选目录发布；不要把 `curate-day`、`stage-update` 或 `dump-update` 当作正常日常入口。

## pending_publish 恢复语义

`data/state/daily_sync/pending_publish.json` 只表示**确实尚未完成发布的工作**。当前记录包括：

- `changed_trade_dates`
- `revised_symbols`
- `pit_changed`

如果一次失败运行留下 `status=pending` 但三个字段都为空，下一次 `daily-sync` 会把它规范化为 `clear`，不会继续制造“存在待发布数据”的假状态。相反，只要任一字段仍有工作，失败后的下一次日更就会合并这些状态并继续发布，旧的已发布 alias 在整个失败期间保持可用。

## 注册 Windows 任务

先预览任务：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\register_tushare_daily_sync_task.ps1 `
  -PythonExe .\.venv\Scripts\python.exe -RepoRoot (Get-Location).Path -WhatIf
```

检查输出后去掉 `-WhatIf` 正式注册。任务仅在当前用户已登录时运行，错过时间会尽快补跑，失败后每 30 分钟重试，最多 3 次。`TUSHARE_TOKEN` 继续由运行账户的既有配置提供，脚本不接收、输出或保存任何凭据值。

脚本默认使用 `configs/pipeline.standalone.yaml`。Integrated 部署必须显式传入 `-ConfigPath configs/pipeline.integrated.yaml`，不会因任务计划默认值重新依赖 Platform。

Linux systemd user timer 和 macOS launchd agent 使用 `scripts/render_standalone_scheduler.py` 渲染；模板位于 `deploy/systemd/` 与 `deploy/launchd/`。wheel 安装同时提供 `tq-render-scheduler` 和上述模板；systemd timer 的 `18:30` 显式绑定 `Asia/Shanghai`，launchd 则使用 macOS 主机时区。渲染命令只写目标目录，不会自动安装或启动任务，完整命令见 [`standalone_sovereignty.md`](standalone_sovereignty.md)。

## 运维状态

- 最新状态：`data/state/daily_sync/latest.json`
- 单次运行：`data/state/daily_sync/runs/<run_id>/manifest.json`
- 运行日志：`data/state/daily_sync/logs/`
- 待恢复发布：`data/state/daily_sync/pending_publish.json`
- PIT 源指纹状态：`data/state/daily_sync/pit_source_state.json`
- extended 最近运行：`data/state/extended_backfill/last_run.json`
- 数据集状态：`& $RepoPython -m qlib_platform dataset-list`

发布失败时保留当前已发布 alias。先检查单次 manifest、`pending_publish.json` 和 `extended_backfill/last_run.json`，修复根因后再重新运行 `daily-sync`；不要手工改写已发布版本目录。
