---
status: DEPRECATED
owner: research
applies_to_commit: 4f5c5d5
last_verified: 2026-08-28
superseded_by: ../examples/local_qlib_backtest/README.md
---

# 本地 CSI300 / Alpha158 / Qlib `qrun` 回测指南

> DEPRECATED / HISTORICAL. 当前唯一维护的 qrun 教程是
> [examples/local_qlib_backtest](https://github.com/magic-alt/qlib-platform/tree/main/examples/local_qlib_backtest)。
> 本页不得用于当前 fit 参数、数据路径或时间窗口决策。

本文记录 `workflow_local_lightgbm.yaml` 的本地研究流程。它复现 Qlib 官方的
`LightGBM + Alpha158 + TopkDropout` 案例，但绑定当前工程的不可变 Qlib 数据，并使用
A 股交易约束。该流程只生成研究产物和模拟回测，不能用于下单或生产发布。

## 1. 研究范围与数据

当前工作流使用动态 `csi300` 成分股、`Alpha158` 量价特征和 `SH000300` 基准。当前本地
数据版本覆盖 2016-02-01 至 2026-08-10，因此配置采用以下不重叠时间段：

| 数据段 | 区间 | 用途 |
| --- | --- | --- |
| 训练 | 2016-05-03 至 2022-12-30 | 拟合模型和特征处理器 |
| 验证 | 2023-01-03 至 2024-12-31 | LightGBM early stopping |
| 测试 | 2025-01-02 至 2026-08-10 | 预测与信号评价 |
| 组合回测 | 2025-01-02 至 2026-08-07 | T+1 日频执行；末日保留一个后续交易日供 Qlib 结算 |

数据路径在 [workflow_local_lightgbm.yaml](https://github.com/magic-alt/qlib-platform/blob/main/workflow_local_lightgbm.yaml) 的
`qlib_init.provider_uri` 中固定为不可变版本。不要把它改成 `current`、工作目录或未经
`dataset-verify` 验证的数据路径；这样才可复现本报告的输入。

## 2. 回测配置

配置保留官方 `Alpha158 -> LGBModel -> SignalRecord -> SigAnaRecord -> PortAnaRecord` 链路，
但组合端采用以下参数：

| 类别 | 设置 | 含义 |
| --- | --- | --- |
| 组合 | `topk: 10`, `n_drop: 5`, `hold_thresh: 1` | 最多持有 10 只；每日最多替换 5 只；不强制多日锁定 |
| 风险敞口 | `risk_degree: 0.95` | 预留约 5% 现金，避免成本和整手约束导致超买 |
| 成交 | `deal_price: open`, `trade_unit: 100` | 次日开盘模拟成交，按 100 股整手 |
| 流动性 | `volume_threshold: [current, "$volume * 0.05"]` | 单日参与量不超过当日成交量 5% |
| 涨跌停 | `$is_limit_up` / `$is_limit_down` | 使用数据集内各股票的涨跌停标记，覆盖 5%、10% 和 20% 板块规则 |
| 成本 | `open_cost: 0.0001`, `close_cost: 0.0006`, `min_cost: 0` | 买入佣金万一且免五；卖出为佣金万一加印花税万五 |

`ASharePortAnaRecord` 是一个很小的 Qlib YAML 适配器：Qlib 0.9.7 的回测引擎要求
`limit_threshold` 与 `volume_threshold` 为 Python 元组，而 YAML 读入后为列表。适配器仅做
列表到元组的转换，不改变交易策略或回测计算。

因停牌、涨跌停和成交量限制，策略不会为凑满仓位而强制成交。因此“10 只”是严格上限，不是每天必然
恰好 10 只；应以报告的持仓数图和 `positions_normal_1day.pkl` 为准。

## 3. 执行回测

从仓库根目录运行，且只使用仓库虚拟环境：

```bash
TaskMplConfig=$(mktemp -d /private/tmp/qlib-mplconfig.XXXXXX)
MLFLOW_ALLOW_FILE_STORE=true MPLCONFIGDIR="$TaskMplConfig" \
  .venv/bin/qrun workflow_local_lightgbm.yaml \
  -e csi300_lgb_alpha158 \
  -u mlruns/csi300_lgb_alpha158_local
```

`MLFLOW_ALLOW_FILE_STORE=true` 是当前 MLflow 对文件型 `mlruns` 后端的显式兼容开关。
`-u` 将本次实验隔离到独立的 `mlruns` 子目录，避免旧实验目录的损坏元数据干扰新运行。

一次成功运行会保存：

```text
mlruns/<experiment-id>/<run-id>/artifacts/
├── pred.pkl                              # 每日股票预测分数
├── sig_analysis/ic.pkl                   # 每日 Pearson IC
├── sig_analysis/ric.pkl                  # 每日 Rank IC
└── portfolio_analysis/
    ├── report_normal_1day.pkl            # 每日账户、收益、成本和基准
    ├── positions_normal_1day.pkl         # 每日 Qlib Position 快照
    ├── indicators_normal_1day_obj.pkl    # Qlib 逐笔已执行订单指标
    └── port_analysis_1day.pkl            # 超额收益风险分析
```

## 4. 生成和阅读回测报告

`qrun` 原生只写 pickle。以下脚本把这些产物转换为可审计的 Parquet、Markdown、图表和 PDF，
不重新训练或重新回测：

```bash
TaskMplConfig=$(mktemp -d /private/tmp/qlib-report-mplconfig.XXXXXX)
MPLCONFIGDIR="$TaskMplConfig" .venv/bin/python scripts/export_qrun_backtest_report.py \
  --artifact-dir mlruns/<experiment-id>/<run-id>/artifacts \
  --workflow-config workflow_local_lightgbm.yaml \
  --output-dir output/pdf/<report-name> \
  --data-root data
```

输出目录包含：

| 文件 | 内容 |
| --- | --- |
| `backtest_report.md` | 完整设置、指标、图表链接、期末持仓、全部已执行交易和最新预测目标组合 |
| `backtest_report.pdf` | 适合阅读与归档的分页报告；展示最近 50 笔交易 |
| `strategy_audit.parquet` | 全部已执行订单，适合筛选、审计和二次分析 |
| `holdings.parquet` | 每日持仓、权重、数量、价格和持有天数 |
| `portfolio_report.parquet` | 每日账户、收益、成本、换手和基准收益 |
| `report_assets/` | 净值、盈亏回撤、仓位、期末持仓和交易活动图表 |

交易审计来自 Qlib 的 `indicators_normal_1day_obj.pkl`，因此记录的是引擎实际执行的订单。被策略
过滤、停牌或涨跌停而根本未提交给交易所的候选订单不会被误报为成交。

阅读结果时应按顺序检查：

1. **信号质量**：`IC` / `Rank IC` 衡量截面相关性，`ICIR` / `Rank ICIR` 衡量其时间稳定性。正 IC
   不等于可以盈利，必须继续看成本后的组合结果。
2. **策略结果**：比较策略年化收益、基准收益、成本后超额收益、信息比率和最大回撤。报告中的
   `port_analysis_1day.pkl` 是相对基准的风险分析；账户自身最大回撤应查看净值/回撤图。
3. **可成交性**：查看每日持仓数、现金比例、成交填充率、交易数和累计成本。持仓低于 10 或换手偏高时，
   先确认涨跌停、停牌、整手与 5% 成交量约束，而不是直接修改模型。
4. **逐笔交易**：对照交易日期、买卖方向、数量、成交价、成交额、成本和 `FILLED/PARTIAL` 状态。完整明细
   以 `strategy_audit.parquet` 和 Markdown 为准。

## 5. 更换机器学习算法

更换模型时，先复制工作流而不是覆盖已验证的 LightGBM 配置：

```bash
cp workflow_local_lightgbm.yaml workflow_local_<model>.yaml
```

除 `task.model` 外，保持 `provider_uri`、`Alpha158`、数据分段、标签、`port_analysis_config` 和交易约束
不变。这让模型比较只改变一个变量。每次使用新的 `-e` 实验名和新的报告目录。

### Ridge 基线

将 `task.model` 替换为：

```yaml
model:
  class: LinearModel
  module_path: qlib.contrib.model.linear
  kwargs:
    estimator: ridge
    alpha: 1.0
    fit_intercept: false
```

这是低复杂度对照组。可调 `alpha`，但要固定验证/测试区间，并与 LightGBM 使用同一成本与组合参数。

### XGBoost

Qlib 0.9.7 内置 `XGBModel` 的 constructor kwargs 进入 booster；把 `n_estimators` 或
`early_stopping_rounds` 放入该 YAML `kwargs` 不能控制预期的 fit 参数。使用当前 example 的
说明，或使用一体化 `configs/model_profiles/xgboost_cpu_v1.yaml` 与项目 adapter；不要复制本页的
旧 XGBoost 配置。

### PyTorch DNN

先安装并验证 PyTorch，再使用 Qlib 的 `DNNModelPytorch`：

```bash
.venv/bin/python -m pip install -e '.[pytorch]'
```

```yaml
model:
  class: DNNModelPytorch
  module_path: qlib.contrib.model.pytorch_nn
  kwargs:
    loss: mse
    lr: 0.001
    optimizer: adam
    max_steps: 300
    batch_size: 2048
    early_stop_rounds: 30
    eval_steps: 20
    GPU: 0
    pt_model_kwargs:
      input_dim: 157
      layers: [512, 256, 128]
```

`input_dim` 必须与当前 Qlib handler 输出的实际特征列数一致；先用短窗口运行，或按
`examples/benchmarks/MLP/workflow_config_mlp_Alpha158.yaml` 核对，而不能假定它永远等于配置名称中的 158。
在 Apple Silicon 上，原生 `qrun` 的 `DNNModelPytorch` 是否使用 MPS 取决于 Qlib/PyTorch 版本；如需受控的
MPS 运行时选择，使用工程的一体化 `train-select` / `research-run` 和
`configs/model_profiles/pytorch_mps_m5.yaml`。

## 6. `qrun` 与一体化研究流程的边界

本页的 `qrun` 流程用于复现 Qlib 官方案例和快速研究。若需要不可变研究 manifest、walk-forward、
模型 profile、信号审计和研究门禁，请使用仓库的一体化入口：

```bash
.venv/bin/python -m qlib_platform --config configs/pipeline.yaml research-run \
  --mode walk-forward --model-profile configs/model_profiles/xgboost_cpu_v1.yaml
```

可用 profile 位于 `configs/model_profiles/`，包括 Ridge、LightGBM CPU/CUDA/OpenCL、XGBoost 和
PyTorch MPS。切换 profile 不应同时修改数据版本、特征、标签或组合规则；否则结果不再是单一模型比较。
