# Qlib Research / Alpha Factory

本工程是机构级量化平台的 Research Plane：Qlib 负责特征、模型、walk-forward、信号分析和研究组合；
正式组合验证、订单、风控、账本和券商接入由 `platform` / LEAN 负责。核心原则是：

1. 生产环境只消费 `platform` 发布的不可变 `DataRelease`；本仓库的 Tushare 采集仅用于开发和独立测试。
2. Canonical Parquet 是事实层，Qlib Bin 是带版本的派生数据；研究运行固定到不可变 release。
3. Bronze、Silver、Gold 和 Qlib 版本分层，所有阶段均可重跑、校验和审计。
4. 复权价格、成交量和 `factor` 使用同一套可逆公式。
5. 财务数据必须按公告日做 point-in-time 展开，不能按报告期直接回填。
6. 元数据 working view 使用原子替换，保证已发布版本所硬链接的快照不会被后续同步改写。

开发模式仍支持 `Bronze → Silver → Gold → Qlib versions`，并通过 SQLite Registry 管理
dataset alias、lineage 和 research run；生产模式以 `DataRelease → Qlib materialization` 为唯一入口。完整的布局、PIT 口径、迁移与命令说明见
[`docs/qlib_data_platform.md`](docs/qlib_data_platform.md)。LEAN、OMS、交易风控和券商写接口不属于
本仓库边界，详见 [`docs/architecture_boundary.md`](docs/architecture_boundary.md)。

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

生产运行不再配置 Tushare 凭据，而是固定到 `platform` 发布的 DataRelease：

```powershell
$env:QUANT_DATA_ROOT = '<SHARED_DATA_ROOT>'
$env:DATASET_RELEASE_ID = 'ds_<64_HEX_CHARS>'
$env:QLIB_REPO = '<PINNED_QLIB_CHECKOUT>'
```

`TUSHARE_TOKEN` 仅供 `configs/pipeline_tushare_dev.yaml` 的开发/独立测试模式使用。

`configs/pipeline_lean_mysql.yaml` 是迁移期兼容配置，不是最终生产数据契约；新的生产研究运行不得直接从 MySQL 读取历史行情。

## 2. 使用 DataRelease 做生产研究验证

Qlib 会校验 release ID、canonical manifest/component SHA-256、覆盖区间和显式 `qlib_staging` 组件；不会猜测 canonical 表结构。

```powershell
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml source-preflight `
  --start <START> --end <END>
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml dataset-build `
  --start <START> --end <END> --single-thread
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml dataset-verify research-current
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml feature-store --dataset-ref research-current --start 20160104 --end <END>
```

同一正式验证中，Qlib 与 LEAN 必须引用完全相同的 `dataset_release_id`。

## 3. 开发/独立测试模式

生产 TuShare ingestion 已归属 `platform`。只有开发和独立测试可以使用本仓库保留的采集链：

```powershell
& $RepoPython -m tushare_qlib --config configs/pipeline_tushare_dev.yaml init-metadata
& $RepoPython -m tushare_qlib --config configs/pipeline_tushare_dev.yaml backfill --start 20160104 --end <END>
& $RepoPython -m tushare_qlib --config configs/pipeline_tushare_dev.yaml backfill-extended --start 20000101 --end <END> --workers 8
& $RepoPython -m tushare_qlib --config configs/pipeline_tushare_dev.yaml sync-universe --start 20160104 --end <END>
& $RepoPython -m tushare_qlib --config configs/pipeline_tushare_dev.yaml sync-benchmark --symbol SH000300 --start 20160104 --end <END>
& $RepoPython -m tushare_qlib --config configs/pipeline_tushare_dev.yaml dataset-build `
  --start 20160104 --end <END> --single-thread
& $RepoPython -m tushare_qlib --config configs/pipeline_tushare_dev.yaml dataset-verify research-current
```

该配置不得用于生产数据发布；生产调度、daily sync 和 DataRelease publication 由 `platform` 执行。

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

模型的运行时解析、构建、保存、加载与 parity check 统一由 ModelAdapter Registry 管理。可用的 CPU
golden baseline 是 `configs/model_profiles/ridge_golden_v1.yaml`；XGBoost profile 是
`configs/model_profiles/xgboost_cpu_v1.yaml`（通过 `.[xgboost]` 或 `.[all,dev]` 安装）。切换模型只需修改
`experiment.model.profile`，DataRelease、AlphaPack、Label、Split 与 Portfolio contract 保持不变。

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

每个 `oos_predictions.parquet` 同时发布 `oos_predictions.snapshot.json`，以
`data_release_id / alpha_pack_id / feature_snapshot_id / label_spec_id / split_spec_id /
model_id / model_profile_id / fold_id` 和 payload SHA-256 固定身份。文件包含
`datetime / instrument / score / label`；重复键、非有限 score、契约漂移或 payload 篡改都会在组合回测前失败。
`backtest-predictions` 接受 parquet 或 snapshot JSON，并在 sidecar 存在时自动做完整验证。rolling folds 的
snapshot 只有在稳定字段完全一致时才允许拼接，并产生新的 aggregate snapshot。

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


Research Gate 不再把 Pearson `ICIR` 当作唯一的一票否决项。对于 TopkDropout，稳定性检查满足
`ICIR >= 0.50` **或** `Rank ICIR >= 0.50` 即可；两者均未达到生产线、但满足
`ICIR >= 0.30` 或 `Rank ICIR >= 0.40`，且其余硬性研究/组合/lineage 条件均通过时，运行会标记为
`RESEARCH_REVIEW`。该状态保留完整研究证据和最终 holdout，但不会发布执行候选。只有未满足复核下限或
其他硬条件失败时才是 `REJECTED`。

聚合 rolling OOS 的 gate 报告还会写出 `*.daily_ic.csv`（逐日 IC、Rank IC 和截面样本数），并在
`signal_diagnostics.folds` 中列出每个 rolling fold 的 IC / Rank IC / ICIR / Rank ICIR，供排查 regime dependence 和 sampling luck。
研究标签会与策略持有期对齐：默认 `hold_thresh=5` 时使用从 T+1 到 T+6 的 5 日前瞻收益
`Ref($close, -6) / Ref($close, -1) - 1`。固定切分会从原始数据与 Qlib 日历的交集取样，预留
Research Gate 所需的 252 个有效 OOS 观测、标签尾部缓冲和回测下一交易日；日历过旧时会在训练前直接报错，
避免训练完成后才因日历越界失败。

研究运行完成后，可将已有 manifest 导出为双方共享的 Artifact Contract v2 bundle：

```powershell
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml artifact-v2-export `
  data/output/research/<RUN_ID>/manifest.json `
  --output-dir data/output/research/<RUN_ID>/artifact-v2 `
  --git-commit <GIT_COMMIT> `
  --container-digest <CONTAINER_DIGEST>
```

命令会生成不含本机路径的 `qlib_research_bundle.v2.json`，以及仅供受控上传器读取的本地
`qlib_research_bundle.v2.uploads.json` sidecar。Qlib 只能发布到 `RESEARCH_PROMOTED`；
`LEAN_VALIDATED`、Paper 和 Production 状态只能由 `platform` 推进。

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

### 4.1 TargetPortfolio 研究交接边界

`qlib-platform` 只负责从模型分数构建不可变的 `TARGET_PORTFOLIO`，不再读取券商仓位、账户或成交，
也不生成 `ORDER_INTENT`、订单、成交或持仓账本。使用研究策略生成目标组合：

```powershell
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml build-target-portfolio `
  --selection-file data/output/selection_<YYYYMMDD>.csv `
  --trade-date <YYYY-MM-DD>
```

正式交接使用 Artifact Contract v2：

```powershell
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml artifact-v2-export `
  data/output/research/<RUN_ID>/manifest.json `
  --output-dir data/output/exports/<RUN_ID> `
  --git-commit <GIT_COMMIT> --container-digest <CONTAINER_DIGEST>
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml lean-register `
  data/output/exports/<RUN_ID>/qlib_research_bundle.v2.json
```

`platform` 校验 DataRelease、payload SHA256、lineage 和 `RESEARCH_PROMOTED` 状态后，才可创建
LEAN validation draft。组合构造、hard risk、Paper、OMS、QMT、订单、成交与 ledger 全部属于 `platform`。
P3 已物理移除旧的 execution/broker/ledger/QMT Python 模块；Qlib 不再创建订单或维护交易状态。

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

`train-select` 和 walk-forward 的 `selection_*.csv` 是研究 OOS artifact，不能作为实盘指令。
每日推理仅生成模型分数、信号健康报告和可交接的研究 Artifact：

```powershell
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml live-inference --as-of <YYYY-MM-DD> `
  --dataset-ref <DATA_RELEASE_ID> --deployment-id <LOCAL_MODEL_RELEASE_ID>
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml daily-signal-run --as-of <YYYY-MM-DD>
```

模型在本仓库内的 `model-deploy` 仅表示本地推理激活，不代表 platform 的 `PRODUCTION` 状态。
Qlib 的最高晋级状态仍为 `RESEARCH_PROMOTED`。LEAN validation、Paper 和 Production 晋级必须在
`platform` 完成。

## 6. 注意事项

- `stock_st` 历史覆盖存在起始时间限制；早期回测需使用历史名称/风险警示数据补齐。
- `index_weight` 是月度快照，构造指数 PIT 股票池时应明确其时间分辨率。
- 示例手续费只是可配置假设，不代表任何券商的真实费率。
- 生产回测建议按历史费用制度分段，而不是用单一费率覆盖全部年份。
- 正常发布使用 `dataset-build`（全量）或 `daily-sync`（日常增量/修复）；低层 `dump-update` 仅用于受控恢复。
