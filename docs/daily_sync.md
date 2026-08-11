# TuShare 每日数据同步

daily-sync 是一次性、幂等的数据发布命令。Windows 任务计划每天 18:30
调用它；节假日或没有内容变化时以 noop 正常结束。

## 数据口径

- raw/daily 保存未复权 OHLC、成交量和成交额。
- raw/adj_factor 保存 TuShare 复权因子。
- raw/dividend 按股票保存分红送转事件。
- Qlib 的 close 保持首日归一化的稳定总回报序列，且 close / factor
  必须能够还原未复权价格。
- export-kline 根据本地 raw 和指定结束日期动态计算 qfq/hfq，不访问远端 API。

已有交易日的数据发生变化时，旧分区会进入 data/raw_revisions。Qlib 发布在
copy-on-write 候选目录完成；新日期使用 dump_update，已有日期修订使用
dump_fix，冒烟验证成功后才替换正式数据集。

## 首次准备

先完成现有全量 raw/Qlib 初始化，再补齐公司行为历史：

    .\.venv\Scripts\python.exe -m tushare_qlib --config configs\pipeline.yaml sync-dividends --bootstrap --resume

验证每日任务而不写正式 raw 或 Qlib：

    .\.venv\Scripts\python.exe -m tushare_qlib --config configs\pipeline.yaml daily-sync --check-only

## 注册 Windows 任务

先预览任务：

    powershell -ExecutionPolicy Bypass -File scripts\register_tushare_daily_sync_task.ps1 -PythonExe .\.venv\Scripts\python.exe -RepoRoot (Get-Location).Path -WhatIf

检查输出后去掉 -WhatIf 正式注册。

任务仅在当前用户已登录时运行，错过时间会尽快补跑，失败后每 30 分钟重试，
最多 3 次。TUSHARE_TOKEN 继续由运行账户的既有配置提供，脚本不接收、输出
或保存任何凭据值。

## 运维状态

- 最新状态：data/state/daily_sync/latest.json
- 单次运行：data/state/daily_sync/runs/<run_id>/manifest.json
- 运行日志：data/state/daily_sync/logs/
- 待恢复发布：data/state/daily_sync/pending_publish.json

失败不会替换当前 Qlib 数据集；下一次运行会继续发布已落盘但尚未进入 Qlib 的修订。
