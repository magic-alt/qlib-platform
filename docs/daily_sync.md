---
status: ACTIVE
owner: operations
applies_to_commit: 8692afefe1f6cc82ab1f276fca788888f9f30f3e
last_verified: 2026-08-28
---

# TuShare 每日数据同步

`daily-sync` 是面向 Tushare 数据源的幂等、状态变更发布命令。Windows 任务计划可在每日 18:30 调用它；节假日或没有内容变化时以 `noop` 正常结束。只在已授权的业务窗口运行它。

## 当前数据平台口径

- `data/bronze/tushare/current/` 是完整且唯一的本地 raw working view；变更分区会在此原子替换，
  不再生成平行的 `revisions/` 数据集。可复现性由发布时冻结的 `data/bronze/versions/` 和
  immutable DataRelease 保证。
- 每次成功发布都会冻结 Bronze、Silver、Gold 快照，并把父版本关系写入 Registry，随后发布不可变的 `data/qlib/versions/<version_id>/`。
- `research-current` 只能指向已发布版本。daily-sync 失败时不会替换该 alias；失败前已落盘的变更由 `data/state/daily_sync/pending_publish.json` 记录以便下次恢复。
- 股票主数据和交易日历等元数据 working view 以原子替换写入，因此后续同步不会改写已发布版本硬链接的快照。
- Qlib close 使用首日归一化的稳定总回报序列；`export-kline` 可由本地原始数据生成 qfq/hfq 视图，不访问远端 API。

## 首次准备与检查

从仓库根目录使用本地解释器：

```powershell
$RepoPython = '.\.venv\Scripts\python.exe'
& $RepoPython -m tushare_qlib sync-dividends --bootstrap --resume
& $RepoPython -m tushare_qlib daily-sync --check-only
```

`--check-only` 只验证数据源与数据质量，不写入正式 Bronze 或 Qlib 版本。首次全量发布或结构性修复请使用
`dataset-build`；迁移既有布局时，先按 [`qlib_data_platform.md`](qlib_data_platform.md) 的 dry run 检查，再显式执行迁移。

## 日常发布

```powershell
& $RepoPython -m tushare_qlib daily-sync --as-of <YYYY-MM-DD>
& $RepoPython -m tushare_qlib dataset-verify research-current
& $RepoPython -m tushare_qlib dataset-resolve research-current
```

每日同步会检查最近交易日，并在最近 60 个交易日内自动补抓缺失的 raw daily 分区；停跑超过该窗口时，
先使用显式 `backfill --start YYYYMMDD --end YYYYMMDD` 补齐，再运行 `daily-sync`。它还会补齐公司行为和
因子历史、刷新参考元数据，并按需要创建新的 immutable Qlib 版本。发布前会按完整交易日历验证
`daily`、`adj_factor`、`daily_basic` 在 raw `current` 中无缺口，并对本次涉及分区深检 manifest、文件
SHA-256、行列数、必需字段、唯一键和分区日期；失败会保留 `pending_publish.json` 并 fail closed。
同时会验证本次 changed trade dates 全部进入候选 Qlib 日历；存在中间缺口时不会切换
`research-current`。追加交易日和历史修订都通过 copy-on-write 候选目录发布；不要把 `curate-day`、
`stage-update` 或 `dump-update` 当作正常日常入口。

## 注册 Windows 任务

先预览任务：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\register_tushare_daily_sync_task.ps1 `
  -PythonExe .\.venv\Scripts\python.exe -RepoRoot (Get-Location).Path -WhatIf
```

检查输出后去掉 `-WhatIf` 正式注册。任务仅在当前用户已登录时运行，错过时间会尽快补跑，失败后每 30 分钟重试，最多 3 次。`TUSHARE_TOKEN` 继续由运行账户的既有配置提供，脚本不接收、输出或保存任何凭据值。

脚本默认使用 `configs/pipeline.standalone.yaml`。Integrated 部署必须显式传入
`-ConfigPath configs/pipeline.integrated.yaml`，不会因任务计划默认值重新依赖 Platform。

Linux systemd user timer 和 macOS launchd agent 使用
`scripts/render_standalone_scheduler.py` 渲染；模板位于 `deploy/systemd/` 与
`deploy/launchd/`。wheel 安装同时提供 `tq-render-scheduler` 和上述模板；systemd timer 的
`18:30` 显式绑定 `Asia/Shanghai`，launchd 则使用 macOS 主机时区。渲染命令只写目标目录，
不会自动安装或启动任务，完整命令见
[`standalone_sovereignty.md`](standalone_sovereignty.md)。

## 运维状态

- 最新状态：`data/state/daily_sync/latest.json`
- 单次运行：`data/state/daily_sync/runs/<run_id>/manifest.json`
- 运行日志：`data/state/daily_sync/logs/`
- 待恢复发布：`data/state/daily_sync/pending_publish.json`
- 数据集状态：`& $RepoPython -m tushare_qlib dataset-list`

发布失败时保留当前 `research-current`。先检查单次 manifest 和 `pending_publish.json`，修复根因后再重新运行 `daily-sync`；不要手工改写已发布版本目录。
