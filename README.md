# Tushare Pro 接入 Qlib：A股日频回测与选股示例工程

本工程把 Tushare Pro 作为数据源，把 Qlib 作为因子、模型、组合和回测框架。核心原则是：

1. Tushare 只负责采集；训练和回测不直接访问远端 API。
2. 原始层、整理层、Qlib 二进制层分离，所有阶段均可重跑和审计。
3. 复权价格、成交量和 `factor` 使用同一套可逆公式。
4. 财务数据必须按公告日做 point-in-time 展开，不能按报告期直接回填。
5. 全量构建与每日增量使用不同 staging 目录。

## 1. 安装

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install --break-system-packages --no-deps -e .[dev]
cp .env.example .env
```

如 `tq` 命令未生效，可直接用仓库内入口：

```bash
./tq --config configs/pipeline.yaml --help
python3 -m tushare_qlib --config configs/pipeline.yaml --help
```

克隆 Qlib 源码并固定版本：

```bash
git clone https://github.com/microsoft/qlib.git
cd qlib
git checkout 79633dd9506ea689e5400dea0197717b5b3d74b7
```

在 `.env` 配置 `TUSHARE_TOKEN`、`QLIB_REPO` 和 `QLIB_DATA_URI`。

如果你已有 lean-platform 的 MySQL 数据库，可切换为数据库模式：

```bash
cp .env.example .env
# 配置 LEAN_MYSQL_*（或 LEAN_MYSQL_DSN）
sed -i '' 's/^  kind: tushare/  kind: lean_mysql/' configs/pipeline.yaml
```

数据库模式下，`backfill/curate/stage/dump` 流程仍复用同一套本地标准化与 Qlib 打包流程，不需要重新从 Tushare 下载。

## 2. 先做一次增量验证（推荐）

先做缩短验证周期的冒烟验证，命令如下（示例 `20250101-20260805`）：

```bash
tq --config configs/pipeline.yaml backfill --start 20250101 --end 20260805
tq --config configs/pipeline.yaml curate --start 20250101 --end 20260805
tq --config configs/pipeline.yaml stage-full --force
tq --config configs/pipeline.yaml dump-full
```

若上述步骤通过，再执行全量构建（与日常首次一致）：

## 3. 首次全量构建

```bash
tq --config configs/pipeline.yaml init-metadata
tq --config configs/pipeline.yaml backfill --start 20160101 --end 20260804
tq --config configs/pipeline.yaml curate
tq --config configs/pipeline.yaml stage-full --force
tq --config configs/pipeline.yaml dump-full
```

## 4. 训练、回测和选股

YAML 工作流（仅用于探索和调试，不产生可准入执行链路的 artifact）：

```bash
export QLIB_DATA_URI=/absolute/path/to/data/qlib/cn_tushare_v1
qrun configs/workflow_lightgbm.yaml
```

或 Python 一体化流程：

```bash
tq --config configs/pipeline.yaml train-select
```

一体化 runner 默认读取 `configs/model_profiles/lightgbm_auto.yaml`。模型家族由 profile 固定，`auto`
只选择该模型可用的执行设备，不会因为换机器而把 LightGBM 改成 DNN。Linux 上会用一个极小训练任务验证
当前 LightGBM 是否真的包含 CUDA backend；探测失败或在 macOS/Windows 上运行时会回退 CPU，并把原因写入
运行 manifest。显式指定 CUDA 或 MPS 时不会静默回退。

```bash
# Apple Silicon：CPU LightGBM
tq --config configs/pipeline.yaml train-select \
  --model-profile configs/model_profiles/lightgbm_cpu_m5.yaml

# Linux / WSL2 + CUDA build：NVIDIA LightGBM
tq --config configs/pipeline.yaml research-run --mode walk-forward \
  --model-profile configs/model_profiles/lightgbm_cuda_nvidia.yaml

# Apple Silicon：Qlib DNN + PyTorch MPS
pip install -e '.[pytorch]'
tq --config configs/pipeline.yaml train-select \
  --model-profile configs/model_profiles/pytorch_mps_m5.yaml
```

CPU/CUDA LightGBM profiles 都使用 `max_bin=63`，因此可以在相同模型参数下比较耗时与指标。DNN 的输入
维度由 `TushareAlpha158Daily` 的实际字段数动态注入，不能按标准 Alpha158 固定写成 158。

每次运行会在 `data/output/research/<model_id>/timings.json`、manifest、MLflow 和命令行 JSON 中记录
`data / train / predict / signal_analysis / backtest / artifact_export` 耗时；Markdown/PDF 报告也会展示设备、
降级原因和阶段耗时。`backtest` 包含 Qlib `PortAnaRecord` 绑定执行的组合风险/指标分析，且不代表回测已在
GPU 上运行。报告渲染自身不计入阶段合计。

一体化流程会自动从 OOS prediction、label 和组合报告计算 Research Gate。只有全部阈值通过且 lineage 完整的
运行才标记为 `PROMOTED`，并发布 `data/output/selection_YYYYMMDD.csv` 与
`data/output/signals/signal_scores_YYYYMMDD.parquet`；未通过的运行保留 manifest、回测产物和
`research_gate.json` 后失败退出，不会生成执行候选。

所有可进入执行链路的文件使用 schema `2.0`，并携带 `artifact_type / promotion_status / run_id / model_id` 与
`dataset_id / lineage_id / manifest_path`。`selection_*.csv` 的类型是 `MODEL_TOPK`，仍只表示模型 TopN；完整分数
文件的类型是 `MODEL_SCORE`，才是 TopkDropout 精确决策的合法输入。旧文件、`REJECTED` 模型、lineage 缺失或
把 `MODEL_TOPK` 直接传给订单生成器都会失败关闭。

升级后需要先重新执行 `stage-full → dump-full` 生成 schema `2.0` 的 `dataset_manifest.json`；旧数据集 manifest
缺少 source snapshot 或 Qlib commit 时，Research Gate 会按 lineage 不完整拒绝发布。

回测运行还会在 `data/output/research/<model_id>/strategy_audit.parquet` 输出“候选 → 指令 → 成交 → 持仓”的审计链。

每次通过 `tq` 一体化流程运行的回测还会在同一运行目录生成可直接阅读的：

- `backtest_report.md`：含图表、期末仓位、最新目标组合和全部逐笔委托/成交明细；
- `backtest_report.pdf`：适合归档与分享的分页版本；
- `report_assets/`：Markdown 引用的权益、盈亏、仓位、持仓权重和交易活动图表。

命令行会优先打印这两个报告路径。已完成的固定切分运行可补生成报告：

```bash
tq --config configs/pipeline.yaml research-report data/output/research/<run_id>
```

若旧运行未导出 `holdings.parquet`，命令会自动查找本地 MLflow 的 Qlib 仓位快照；存在多个候选时传入
`--positions-file path/to/positions_normal_1day.pkl`。

### 4.1 TopkDropout 实盘决策与持仓对账

`TopkDropoutStrategy(topk=30, n_drop=5, hold_thresh=5)` 每日重算排名，但不是每日清仓重买 Top30。
精确模式用 T−1 分数、券商 T−1 仓位和 T 日报价生成有限换仓的订单；原有 `build-trade-plan` 仍是独立的
风险配权路径，只发布 `TARGET_PORTFOLIO`/`STRATEGY_DECISION`，不再发布可执行 `ORDER_INTENT`。

先以券商仓位快照和成交回报更新本地持有期账本：

```bash
tq --config configs/pipeline.yaml reconcile-holdings broker_positions.csv \
  --fills broker_fills.csv --as-of-date 2026-08-08
```

仓位 CSV 必须包含 `instrument,quantity,available_quantity`；成交 CSV 必须包含
`fill_id,trade_date,instrument,side,quantity,fill_price`。首次导入的既有仓位没有可追溯买入成交时，额外传入
`--initial-holdings`（字段：`instrument,opened_trade_date`）。未能解释的券商持仓会失败关闭，避免错误绕过
`hold_thresh`。

在交易日读取完整分数、对账后仓位、报价和可用现金生成决策及订单：

```bash
tq --config configs/pipeline.yaml build-topk-orders \
  data/output/signals/signal_scores_20260808.parquet \
  data/output/holdings_state_20260808.csv trade_quotes.csv \
  --cash 1000000 --daily-pnl-pct -0.002
```

该命令生成 `strategy_decision_YYYYMMDD.csv`、`orders_YYYYMMDD.csv` 和
`blocked_orders_YYYYMMDD.csv`。`--daily-pnl-pct` 是当日券商账户收益率，缺失时订单发布失败关闭。报价需包含
`instrument,price,paused,is_limit_up,is_limit_down,sector`；可选
`adv20_volume` 以 `ADV20 × max_participation_rate` 约束实盘委托。回测仍使用当日实际成交量，因此审计文件应作为
两种流动性口径的对照，而不是把回测成交量当作开盘前可知信息。

### 4.2 SH000300 基准回测结果解读（最新一次）

本次使用 `--benchmark SH000300`，仅调整基准，不改仓位/模型参数的情况下跑通了 3 年滚动样本，产出文件：

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

以 20260805 为例：

```bash
tq --config configs/pipeline.yaml backfill --start 20260805 --end 20260805
tq --config configs/pipeline.yaml curate-day 20260805
tq --config configs/pipeline.yaml stage-update 20260805
tq --config configs/pipeline.yaml dump-update
tq --config configs/pipeline.yaml train-select
```

生产环境应增加：交易日判断、数据到齐检查、原始分区哈希、Qlib 查询冒烟测试、模型版本锁定和告警。

## 6. 注意事项

- `stock_st` 历史覆盖存在起始时间限制；早期回测需使用历史名称/风险警示数据补齐。
- `index_weight` 是月度快照，构造指数 PIT 股票池时应明确其时间分辨率。
- 示例手续费只是可配置假设，不代表任何券商的真实费率。
- 生产回测建议按历史费用制度分段，而不是用单一费率覆盖全部年份。
- `dump-update` 仅用于在现有字段集合上追加新交易日；新增字段或修复历史数据应构建新版本数据集。
