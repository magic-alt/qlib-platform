# Tushare Pro 接入 Qlib：A股日频回测与选股示例工程

本工程把 Tushare Pro 作为数据源，把 Qlib 作为因子、模型、组合和回测框架。核心原则是：

1. Tushare 只负责采集；训练和回测不直接访问远端 API。
2. Parquet 是事实层，Qlib Bin 是带版本的派生数据；研究运行固定到不可变版本。
3. Bronze、Silver、Gold 和 Qlib 版本分层，所有阶段均可重跑、校验和审计。
4. 复权价格、成交量和 `factor` 使用同一套可逆公式。
5. 财务数据必须按公告日做 point-in-time 展开，不能按报告期直接回填。
6. 元数据 working view 使用原子替换，保证已发布版本所硬链接的快照不会被后续同步改写。

当前 Qlib 数据平台采用 `Bronze → Silver → Gold → Qlib versions`，通过 SQLite Registry 管理
dataset alias、lineage 和 research run。完整的布局、PIT 口径、迁移与命令说明见
[`docs/qlib_data_platform.md`](docs/qlib_data_platform.md)。LEAN、OMS、交易风控和券商写接口不属于
本次 Qlib 数据平台边界。

## 1. 安装

```bash
python3.12 -m venv .venv
# macOS/Linux: .venv/bin/python -m pip install -e '.[all,dev]'
# Windows PowerShell:
$RepoPython = '.\.venv\python.exe'
& $RepoPython -m pip install -e '.[all,dev]'
# Install the fixed Qlib checkout into this same environment:
# <venv-python> -m pip install -e <path-to-qlib>
# macOS: cp .env.example .env
# Windows PowerShell: Copy-Item .env.example .env
```

所有项目命令均通过仓库本地解释器运行；Windows PowerShell 后续示例均假定已设置 `$RepoPython`：

```powershell
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml --help
```

克隆 Qlib 源码并固定版本：

```bash
git clone https://github.com/microsoft/qlib.git
cd qlib
git checkout 79633dd9506ea689e5400dea0197717b5b3d74b7
```

在 `.env` 配置 `TUSHARE_TOKEN`、`QLIB_REPO` 和 `QLIB_DATA_URI`。

如果你已有 lean-platform 的 MySQL 数据库，可切换为数据库模式：

```powershell
# 配置 LEAN_MYSQL_*（或 LEAN_MYSQL_DSN）
Copy-Item .env.example .env
# 编辑 .env 后先做只读覆盖检查；不完整区间会列出具体缺口并阻止 backfill。
& $RepoPython -m tushare_qlib --config configs/pipeline_lean_mysql.yaml source-preflight --start <START> --end <END>
& $RepoPython -m tushare_qlib --config configs/pipeline_lean_mysql.yaml init-metadata
& $RepoPython -m tushare_qlib --config configs/pipeline_lean_mysql.yaml sync-universe --start <START> --end <END>
& $RepoPython -m tushare_qlib --config configs/pipeline_lean_mysql.yaml backfill --start <START> --end <END>
& $RepoPython -m tushare_qlib --config configs/pipeline_lean_mysql.yaml sync-benchmark --symbol SH000300 `
  --start <START> --end <END>
& $RepoPython -m tushare_qlib --config configs/pipeline_lean_mysql.yaml backfill-extended --start <START> --end <END>
& $RepoPython -m tushare_qlib --config configs/pipeline_lean_mysql.yaml dataset-build `
  --start <START> --end <END> --single-thread
& $RepoPython -m tushare_qlib --config configs/pipeline_lean_mysql.yaml dataset-verify research-current
```

数据库模式下，`dataset-build` 仍复用同一套本地标准化、PIT 物化和版本化 Qlib 打包流程，不需要重新从 Tushare 下载。`curate`、`stage-*` 和 `dump-*` 只保留作低层恢复工具。
`lean_canonical_v1` 会直接使用 lean-platform 的 PIT 成分有效期、派生交易状态来源以及 CNY/股数单位，
并按区间批量读取，避免逐交易日重复扫描 MySQL 大表。正式训练仍必须满足 `research.min_history_days`
和研究准入门槛；短窗口只能通过显式 `--train/--valid/--test` 用作 smoke 回测。

## 2. 先做一次增量验证（推荐）

先在显式、已验证的日期窗口内做冒烟验证。下例使用当前配置的初始窗口；请按本地数据实际覆盖范围替换 `<END>`：

```powershell
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml init-metadata
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml sync-universe --start 20160104 --end <END>
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml backfill --start 20160104 --end <END>
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml backfill-extended --start 20000101 --end <END> --workers 8
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml sync-benchmark --symbol SH000300 --start 20160104 --end <END>
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml dataset-build --start 20160104 --end <END>
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml dataset-verify research-current
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml feature-store --dataset-ref research-current --start 20160104 --end <END>
```

完成首次构建后，可将 TuShare 下载和 Qlib 数据发布作为独立后台任务。先运行
`sync-dividends --bootstrap --resume` 补齐公司行为，再用 `daily-sync --check-only`
验证每日检查。Windows 任务注册、恢复和数据口径见 docs/daily_sync.md。

主 Qlib 使用稳定总回报价格；如需查看以指定结束日为锚点的最新前复权 K 线，
使用本地 export-kline --adjust qfq。

若上述步骤通过，再执行全量构建（与日常首次一致）：

## 3. 首次全量构建

```powershell
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml init-metadata
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml backfill --start 20160104 --end <END>
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml backfill-extended --start 20000101 --end <END> --workers 8
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml sync-universe --start 20160104 --end <END>
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml sync-benchmark --symbol SH000300 --start 20160104 --end <END>
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml dataset-build `
  --start 20160104 --end <END> --single-thread
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml dataset-verify research-current
```

## 4. 训练、回测和选股

YAML 工作流（仅用于探索和调试，不产生可准入执行链路的 artifact）：

```powershell
# 使用 dataset-resolve 输出的不可变 Qlib 数据集路径。
$env:QLIB_DATA_URI = '<RESOLVED_QLIB_DATASET>'
& .\.venv\Scripts\qrun.exe configs\workflow_lightgbm.yaml
```

或 Python 一体化流程：

```powershell
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml train-select
```

Windows 多进程研究任务必须通过 `& $RepoPython -m tushare_qlib` 或带
`if __name__ == "__main__":` 保护的 `.py` 文件启动。项目默认显式使用 Joblib `loky`；
当 `qlib_kernels > 1` 时，stdin/管道（`python -`）和 `python -c` 会在 Qlib 初始化前失败，
避免 Windows `spawn` 进入异常 worker/resource-tracker 生命周期。`qlib_kernels: 1` 仅用于短区间隔离诊断。

一体化 runner 默认读取 `configs/model_profiles/lightgbm_auto.yaml`。模型家族由 profile 固定，`auto`
只选择该模型可用的执行设备，不会因为换机器而把 LightGBM 改成 DNN。Linux 会探测 CUDA backend，Windows
会探测 OpenCL `gpu` backend；两者都执行一个真实的一棵树训练，而不是只检查显卡。`auto` 探测失败会回退 CPU
并写入 manifest；显式指定 CUDA、OpenCL GPU 或 MPS 时不会静默回退。

```powershell
# Apple Silicon：CPU LightGBM
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml train-select `
  --model-profile configs/model_profiles/lightgbm_cpu_m5.yaml

# Linux / WSL2 + CUDA build：NVIDIA LightGBM
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml research-run --mode walk-forward `
  --model-profile configs/model_profiles/lightgbm_cuda_nvidia.yaml

# Windows 原生 OpenCL：先按 docs/windows_lightgbm_gpu.md 编译，再验证
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml runtime-probe `
  --model-profile configs/model_profiles/lightgbm_gpu_windows.yaml
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml train-select `
  --model-profile configs/model_profiles/lightgbm_gpu_windows.yaml

# Apple Silicon：Qlib DNN + PyTorch MPS
& $RepoPython -m pip install -e '.[pytorch]'
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml train-select `
  --model-profile configs/model_profiles/pytorch_mps_m5.yaml
```

CPU/CUDA/OpenCL LightGBM profiles 都使用 `max_bin=63`，因此可以在相同模型参数下比较耗时与指标。DNN 的输入
维度由 `TushareAlpha158Daily` 的实际字段数动态注入，不能按标准 Alpha158 固定写成 158。

每次运行会在 `data/output/research/<model_id>/timings.json`、manifest、MLflow 和命令行 JSON 中记录
`qlib_init / feature_store / handler_process / train / model_save / predict / signal_analysis /
benchmark_load / portfolio_engine /
artifact_export / report` 耗时、wall time、peak RSS 与 LightGBM best iteration；Markdown/PDF 报告也会展示设备、
降级原因和阶段耗时。timings 还记录 Audit quote query/transform、audit build、holdings build 等不重复计入
total 的诊断子阶段，以及各顶层阶段的 handles/threads/children 变化。`portfolio_engine` 包含 Qlib
`PortAnaRecord` 绑定执行的 Exchange 初始化和 simulator loop，且不代表回测已在 GPU 上运行。

大批量特征实验先使用不运行 portfolio backtest、也绝不发布 selection 的 Signal Screen：

```powershell
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml research-run --mode fixed --stage signal `
  --model-profile configs/model_profiles/lightgbm_cpu_fast.yaml
```

Signal Screen 生成的 immutable OOS prediction 可以独立测试策略参数，不会重新创建 Dataset 或训练模型：

```powershell
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml backtest-predictions `
  data/output/research/<run_id>/oos_predictions.parquet `
  --topn 30 --artifact-level minimal
```

`minimal` 保存 prediction、组合日表、持仓摘要、策略 audit 和 timings；`full` 额外渲染 Markdown/PDF。
walk-forward 的 rolling folds 只训练并保存 OOS prediction/label，不运行各自独立的组合回测。系统将所有 rolling
prediction 严格按日期拼接并校验无重叠后，仅运行一次 `minimal` 的连续账户回测；只有独立 final holdout 使用
`full`。

Signal Gate 通过后，再运行完整 fixed/walk-forward portfolio Gate。默认 walk-forward 使用 1500 日 train、126 日
valid、63 日 test，累计 252 日 rolling OOS 与独立 252 日 final holdout，并统一使用 6 日 purge/embargo/label buffer。

一体化流程会自动从 OOS prediction、label 和组合报告计算 Research Gate。Signal 指标来自拼接后的完整 OOS
prediction/label；Portfolio 指标只来自同一信号流驱动的单账户连续回测，不再复合各 fold 的独立账户收益。
运行产出的 `fold_boundary_continuity.json` 会逐个边界验证未交易持仓的 `holding_days` 没有回退。只有全部阈值
通过且 lineage 完整的
运行才标记为 `PROMOTED`，并发布 `data/output/selection_YYYYMMDD.csv` 与
`data/output/signals/signal_scores_YYYYMMDD.parquet`；未通过的运行保留 manifest、回测产物和
`research_gate.json` 后失败退出，不会生成执行候选。

研究标签会与策略持有期对齐：默认 `hold_thresh=5` 时使用从 T+1 到 T+6 的 5 日前瞻收益
`Ref($close, -6) / Ref($close, -1) - 1`。固定切分会从原始数据与 Qlib 日历的交集取样，预留
Research Gate 所需的 252 个有效 OOS 观测、标签尾部缓冲和回测下一交易日；日历过旧时会在训练前直接报错，
避免训练完成后才因日历越界失败。

所有可进入执行链路的文件使用 schema `2.0`，并携带 `artifact_type / promotion_status / run_id / model_id` 与
`dataset_id / lineage_id / manifest_path`。`selection_*.csv` 的类型是 `MODEL_TOPK`，仍只表示模型 TopN；完整分数
文件的类型是 `MODEL_SCORE`，才是 TopkDropout 精确决策的合法输入。旧文件、`REJECTED` 模型、lineage 缺失或
把 `MODEL_TOPK` 直接传给订单生成器都会失败关闭。

升级后需要先重新执行 `dataset-build` 生成带完整 lineage 的当前版本 manifest；旧数据集 manifest
缺少 source snapshot 或 Qlib commit 时，Research Gate 会按 lineage 不完整拒绝发布。

回测运行还会在 `data/output/research/<model_id>/strategy_audit.parquet` 输出“候选 → 指令 → 成交 → 持仓”的审计链。

每次通过仓库本地解释器运行的一体化回测还会在同一运行目录生成可直接阅读的：

- `backtest_report.md`：含图表、期末仓位、最新目标组合和全部逐笔委托/成交明细；
- `backtest_report.pdf`：适合归档与分享的分页版本；
- `report_assets/`：Markdown 引用的权益、盈亏、仓位、持仓权重和交易活动图表。

命令行会优先打印这两个报告路径。已完成的固定切分运行可补生成报告：

```powershell
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml research-report data/output/research/<run_id>
```

若旧运行未导出 `holdings.parquet`，命令会自动查找本地 MLflow 的 Qlib 仓位快照；存在多个候选时传入
`--positions-file path/to/positions_normal_1day.pkl`。

### 4.1 TopkDropout 实盘决策与持仓对账

`TopkDropoutStrategy(topk=30, n_drop=5, hold_thresh=5)` 每日重算排名，但不是每日清仓重买 Top30。
精确模式用 T−1 分数、T 日盘前券商仓位快照和 T 日报价快照生成有限换仓的订单；原有 `build-trade-plan` 仍是独立的
风险配权路径，只发布 `TARGET_PORTFOLIO`/`STRATEGY_DECISION`，不再发布可执行 `ORDER_INTENT`。

先以券商仓位快照和成交回报更新本地持有期账本：

```powershell
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml reconcile-holdings broker_positions.csv `
  --fills broker_fills.csv --as-of-date 2026-08-10
```

`broker_positions.csv` 是 execution position snapshot，必须包含：

- `instrument,quantity,available_quantity`：券商可执行仓位；
- `as_of_trade_date`：该快照所属交易日，必须与 `--as-of-date` 一致；
- `snapshot_at_utc`：券商原始仓位响应的 UTC 采集时刻，不能填对账完成时刻；
- `account_id`：可选但推荐；
- `source`：可选，取值为 `broker` 或 `paper`，省略时按 `broker` 处理。

对账输出可直接作为 `build-topk-orders` 的 positions 输入，字段为
`instrument,quantity,available_quantity,holding_days,opened_trade_date,as_of_trade_date,snapshot_at_utc`，并保留可选
`account_id` 与 `source`。持有期账本内部仍使用 `last_quantity` 和 `as_of_date`；这两个内部字段不会泄漏到执行快照。

成交 CSV 必须包含 `fill_id,trade_date,instrument,side,quantity,fill_price`。首次导入的既有仓位没有可追溯买入成交时，
额外传入 `--initial-holdings`（字段：`instrument,opened_trade_date`）。未能解释的券商持仓会失败关闭，避免错误绕过
`hold_thresh`。

在交易日读取完整分数、对账后仓位、报价和可用现金生成决策及订单：

```powershell
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml build-topk-orders `
  data/output/signals/signal_scores_20260807.parquet `
  data/output/holdings_state_20260810.csv trade_quotes.csv `
  --cash 1000000 --daily-pnl-pct -0.002
```

该命令生成 `strategy_decision_YYYYMMDD.csv`、`orders_YYYYMMDD.csv` 和
`blocked_orders_YYYYMMDD.csv`。`--daily-pnl-pct` 是当日券商账户收益率，缺失时订单发布失败关闭。

`trade_quotes.csv` 是 quote snapshot，必须包含
`instrument,price,paused,is_limit_up,is_limit_down,sector,as_of_trade_date,snapshot_at_utc`。其中
`as_of_trade_date` 必须等于信号 artifact 声明的 `trade_date`，`snapshot_at_utc` 必须是行情源实际采集时刻；仓位和报价
各自只能包含一个快照时刻，并且都必须满足配置的最大 age。可选
`adv20_volume` 以 `ADV20 × max_participation_rate` 约束实盘委托。回测仍使用当日实际成交量，因此审计文件应作为
两种流动性口径的对照，而不是把回测成交量当作开盘前可知信息。

`2026-08-07`（周五）信号对应 `2026-08-10`（周一）交易日；上面的日期仅用于说明文件配对。真实下单前必须使用
当前交易日文件，并在采集券商仓位和行情后立即运行，复用原始 `snapshot_at_utc`，否则 freshness 检查会失败关闭。

完整的 `reconcile → freshness → topk` 回归冒烟命令：

```powershell
& $RepoPython -m pytest -q tests/test_holdings_ledger.py tests/test_live_controls.py tests/test_topk_dropout.py
& $RepoPython -m ruff check src tests
```

### 4.2 SH000300 基准回测结果解读（历史示例）

以下为历史运行示例，不代表当前 `research-current` 版本或最新模型表现。该运行仅调整基准、不改仓位/模型参数，并产出：

`data/output/selection_20260804.csv`

主要指标含义（qrun / qlib 输出）：

- `IC`: 0.007245675923387125。表示预测分数与下期收益的平均相关性接近 0，说明在截面层面方向性预测能力很弱。
- `ICIR`: 0.04548204309194314。`IC / IC_std` 的信息比率，值很低，说明 IC 不稳定，信号可重复性较差。
- `Rank IC`: 0.04895135627373251。基于排序相关性的指标，也很低，说明“排序能力”偏弱。
- `Rank ICIR`: 0.2587141872486941。分位排序信息比率偏低，策略边界更偏近随机。
- `Long-Avg Ann Return`: -0.08653338113799691。仅做多池子年度化收益为负。
- `Long-Avg Ann Sharpe`: -0.35390926148386115。只做多组合年化收益风险比为负，说明收益不够抵消波动。
- `Long-Short Ann Return`: 0.09958028909750283。多空组合年度化收益为正，说明空头对冲后有一定净 alpha 空间。
- `Long-Short Ann Sharpe`: 0.8357154429232966。多空组合的风险调整收益较好（>0.8，可作为可用信号起点）。

基准（SH000300）对照：

- 年化收益: 0.073153
- 信息比率: 0.433658
- 最大回撤: -0.110666

结合解释：

- 该次结果没有做到明显超额收益（仅看 IC/ICIR），但多空结构在风险调整后存在一定正收益。
- 如果目标是做长期跟踪基准，当前特征/参数下更像“中性化套利型”而非单边强势择时。
- 可优先优化方向：提高因子信息密度（调参或新特征）、控制换手、检查交易成本敏感性，并增加更长/多段市场周期验证，避免 2022~2026 区间过拟合。

## 5. 每日增量

日常发布由 `daily-sync` 统一处理近期开奖、公司行为、元数据、增量或修复 staging、版本化 Qlib 发布及
`research-current` alias 更新。仅在已明确授权的交易日运行；示例中的日期须替换为实际业务日：

```powershell
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml daily-sync --as-of <YYYY-MM-DD>
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml dataset-verify research-current
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml train-select --dataset-ref research-current
```

`curate-day`、`stage-update` 和 `dump-update` 是低层恢复工具，不是常规每日发布入口。

`train-select` 和 walk-forward 的 `selection_*.csv` 是研究 OOS artifact，不能作为 T→T+1 实盘提醒。
生产 shadow-signal 使用独立的模型部署与每日推理链：

```powershell
# 低频：由已通过 gate 的 walk-forward release 生成 STAGED bundle，再人工激活
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml model-refit --research-run <RUN_ID> --as-of 2026-08-10
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml model-deploy <DEPLOYMENT_ID>

# 每日收盘：统一入口会先验证交易日，再同步 T 日数据并真正推理 T→T+1
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml production-run --phase close --business-date 2026-08-10

# T+1 盘前：消费日期化 broker/quote/account inbox，生成账户动作提醒
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml production-run --phase pretrade --business-date 2026-08-11

# 历史 parity：必须显式使用冻结至 T 的数据集，不能使用当前完整数据集代替
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml live-inference --as-of 2026-08-10 `
  --dataset-uri data/snapshots/20260810 `
  --deployment-id <DEPLOYMENT_ID> `
  --compare-research data/output/research/<RUN_ID>/oos_predictions.parquet
```

完整调度、inbox 契约、故障恢复、回滚和 20 日 shadow 验收要求见
[`docs/OPERATIONS_RUNBOOK.md`](docs/OPERATIONS_RUNBOOK.md)。P0 只发布人工确认用 `ORDER_INTENT`，不会提交券商订单。

## 6. 注意事项

- `stock_st` 历史覆盖存在起始时间限制；早期回测需使用历史名称/风险警示数据补齐。
- `index_weight` 是月度快照，构造指数 PIT 股票池时应明确其时间分辨率。
- 示例手续费只是可配置假设，不代表任何券商的真实费率。
- 生产回测建议按历史费用制度分段，而不是用单一费率覆盖全部年份。
- 正常发布使用 `dataset-build`（全量）或 `daily-sync`（日常增量/修复）；低层 `dump-update` 仅用于受控恢复。
