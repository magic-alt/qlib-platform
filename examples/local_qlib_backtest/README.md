# 本地 Qlib 数据回测：从数据到自定义机器学习算法

这是一个可直接在 Windows 实机运行的研究案例。它沿用 Qlib 官方的自动研究工作流：

```text
不可变本地数据 -> Alpha158 特征 -> 模型训练 -> test 段预测
              -> SignalRecord / SigAnaRecord -> TopkDropout 模拟回测
```

与 Qlib 官方 CSI300 示例相比，本案例保留 `DatasetH`、`SignalRecord`、`SigAnaRecord` 和
`TopkDropoutStrategy` 主链路，但使用本仓库的 `TushareAlpha158Fundamental`、动态 A 股股票池、
涨跌停字段、成交量约束、100 股整手和次日开盘成交。它只写本地研究记录，不下单、不访问最终
holdout、不生成正式候选，也不发布 `TARGET_PORTFOLIO`。

## 1. 文件说明

| 文件 | 用途 |
| --- | --- |
| `run_backtest.ps1` | 校验本地不可变数据、绑定 `QLIB_DATA_URI` 并执行指定 workflow |
| `workflow_lightgbm.yaml` | 默认树模型案例；适合调学习率、叶子数、正则和采样参数 |
| `workflow_ridge.yaml` | Qlib 内置 Ridge 低复杂度基线 |
| `workflow_custom_ridge.yaml` | 加载当前目录中的自定义模型插件 |
| `custom_model.py` | 一个完整的 `Model.fit/predict` 插件示例 |

三个 workflow 除模型段和实验名外，数据、标签、切分、策略、成本与成交假设保持一致，便于做
单变量模型比较。

## 2. 前置条件

从仓库根目录执行。所有 Python/Qlib 命令只使用仓库本地环境：

```powershell
$RepoPython = '.\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $RepoPython)) {
    throw '缺少 .\.venv\Scripts\python.exe，请先重建仓库本地环境。'
}
& $RepoPython -m pip install -c constraints\ci.txt -e '.[dev]'
```

本案例默认解析本地数据注册表中的 `research-current`，而不是直接读取 `data/qlib/current` 或把
某个易变目录写死到 YAML。脚本会先执行完整 `dataset-verify`；manifest、文件校验和或数据身份不一致
时会停止，不会带病回测。

仅检查当前数据引用而不训练：

```powershell
$env:QLIB_REPO = '.'
$env:QLIB_DATA_URI = 'data/qlib'
& $RepoPython -m tushare_qlib --config configs\pipeline_tushare_dev.yaml dataset-resolve research-current
& $RepoPython -m tushare_qlib --config configs\pipeline_tushare_dev.yaml dataset-verify research-current
```

上面两个环境变量在这两条命令中只是开发配置所需的非敏感本地路径占位；真正传给 qrun 的
`QLIB_DATA_URI` 由 `run_backtest.ps1` 改绑为注册表解析出的不可变版本路径。

## 3. 运行 LightGBM 基线

```powershell
.\examples\local_qlib_backtest\run_backtest.ps1 -Model lightgbm
```

脚本依次完成：

1. 确认 `.venv/Scripts/python.exe` 与 `.venv/Scripts/qrun.exe` 存在；
2. 把 `research-current` 解析成确切版本并执行完整 checksum 校验；
3. 运行 `validate-qrun-contract`，检查策略、成交和基准静态语义；
4. 在 `mlruns/examples_local_backtest` 中创建隔离的 Qlib/MLflow 研究记录；
5. 由 `SignalRecord` 保存预测、`SigAnaRecord` 保存 IC、组合记录器保存模拟回测结果。

也可明确指定数据引用和实验名：

```powershell
.\examples\local_qlib_backtest\run_backtest.ps1 `
    -Model lightgbm `
    -DatasetRef research-current `
    -ExperimentName local_alpha158_lgb_trial_01
```

案例固定采用以下不重叠区间，并给 5 日标签留出 purge gap：

| 段 | 日期 | 作用 |
| --- | --- | --- |
| train | 2018-10-01 至 2024-12-27 | 只用于拟合模型与学习型处理器 |
| gap | 2024-12-28 至 2025-01-07 | 隔离 5 日未来收益标签的前视窗口 |
| valid | 2025-01-08 至 2025-07-02 | early stopping / 超参数判断 |
| gap | 2025-07-03 至 2025-07-10 | 隔离 valid 与 test |
| test / backtest | 2025-07-11 至 2026-08-10 | 只做样本外预测和模拟回测 |

不要因为本地数据更新就把 test 结束日顺延后继续调参；那会把已经观察过的 OOS 结果变成隐性验证集。
需要新窗口时，应先冻结新的研究计划和切分，再运行实验。

## 4. 读取结果

qrun 结束时会在控制台输出 IC 与组合风险指标。完整产物位于：

```text
mlruns/examples_local_backtest/<experiment-id>/<run-id>/artifacts/
├── pred.pkl
├── sig_analysis/
│   ├── ic.pkl
│   └── ric.pkl
└── portfolio_analysis/
    ├── report_normal_1day.pkl
    ├── positions_normal_1day.pkl
    ├── indicators_normal_1day_obj.pkl
    └── port_analysis_1day.pkl
```

如果需要可审计的 Markdown、Parquet、图表和 PDF，可在找到最新 run 的 artifact 目录后执行：

```powershell
& $RepoPython scripts\export_qrun_backtest_report.py `
    --artifact-dir mlruns\examples_local_backtest\<experiment-id>\<run-id>\artifacts `
    --workflow-config examples\local_qlib_backtest\workflow_lightgbm.yaml `
    --output-dir data\output\local_backtest_report `
    --data-root data
```

先看预测质量的 `IC`、`Rank IC` 及稳定性，再看成本后超额收益、信息比率和最大回撤，最后检查成交
填充率、持仓数、现金、换手与逐笔模拟成交。正 IC 不保证扣费后盈利；策略收益也不能证明模型在未见
数据上仍然有效。

## 5. 调整 LightGBM 参数

复制配置后只调整 `task.model.kwargs`，并使用新实验名：

```powershell
Copy-Item examples\local_qlib_backtest\workflow_lightgbm.yaml `
    examples\local_qlib_backtest\workflow_lightgbm_trial.yaml
```

常用参数及影响：

| 参数 | 作用 | 调整建议 |
| --- | --- | --- |
| `learning_rate` / `num_boost_round` | 步长与最大迭代数 | 降低步长通常要增加迭代数，保留 early stopping |
| `num_leaves` / `max_depth` | 模型容量 | 同时增大容易过拟合 |
| `lambda_l1` / `lambda_l2` | 正则化 | 用 valid 段选择，不看 test 调参 |
| `colsample_bytree` / `subsample` | 特征与样本采样 | 可降相关、提速；固定随机种子后比较 |
| `num_threads` | CPU 并行度 | 只影响资源与可复现性能，不应改变研究定义 |

运行复制后的任意 workflow：

```powershell
.\examples\local_qlib_backtest\run_backtest.ps1 `
    -Workflow examples\local_qlib_backtest\workflow_lightgbm_trial.yaml `
    -ExperimentName local_alpha158_lgb_trial_02
```

同一轮模型比较不得同时修改标签、股票池、train/valid/test、成本、策略参数或数据版本。超参数只由
train/valid 决定，test 只做预先约定的一次 OOS 评价。

## 6. 替换机器学习算法

### Qlib 内置 Ridge

Ridge 是检查“复杂模型是否真的带来增量”的稳健基线：

```powershell
.\examples\local_qlib_backtest\run_backtest.ps1 -Model ridge
```

调整正则强度只需修改 `workflow_ridge.yaml` 的 `alpha`。`include_valid: false` 明确禁止把 valid 合并进
训练数据。

### XGBoost

仓库已声明可选依赖。安装后，复制 LightGBM workflow，并只把 `task.model` 替换为：

```yaml
model:
  class: XGBModel
  module_path: qlib.contrib.model.xgboost
  kwargs:
    objective: reg:squarederror
    eval_metric: rmse
    eta: 0.03
    max_depth: 8
    subsample: 0.8
    colsample_bytree: 0.8
    nthread: 8
```

```powershell
& $RepoPython -m pip install -c constraints\ci.txt -e '.[dev,xgboost]'
.\examples\local_qlib_backtest\run_backtest.ps1 `
    -Workflow examples\local_qlib_backtest\workflow_xgboost_trial.yaml `
    -ExperimentName local_alpha158_xgb_trial_01
```

不要照搬 LightGBM 的 `num_leaves`、`lambda_l1` 等参数名。还要注意：Qlib 0.9.7 内置 `XGBModel`
的 qrun 初始化参数会传给 XGBoost booster，而 `fit()` 的 `num_boost_round=1000` 与
`early_stopping_rounds=50` 使用适配器默认值；把这两个键放进 YAML `kwargs` 并不能调整 fit 参数。
需要改变它们时，应仿照 `custom_model.py` 写一个显式保存并转发 fit 参数的适配器，或使用仓库的一体化
`configs/model_profiles/xgboost_cpu_v1.yaml`，不要依赖被 XGBoost 忽略的参数。

### 添加自定义算法

`custom_model.py` 展示最小完整插件：

- 继承 `qlib.model.base.Model`；
- `fit()` 只读取 `train` 的 `DataHandlerLP.DK_L`；
- 保存公开的拟合状态，支持 Qlib recorder 序列化；
- `predict()` 读取 `test` 的 `DataHandlerLP.DK_I` 并返回保留 MultiIndex 的 `Series`；
- 对非有限输入、空训练集、列变化和非法参数 fail closed。

运行它：

```powershell
.\examples\local_qlib_backtest\run_backtest.ps1 -Model custom_ridge
```

要接入自己的算法，复制该类并替换内部 estimator；然后在 workflow 的 `sys.path` 中保留插件目录，修改
`task.model.class` 和 `module_path`。不要让 `fit()` 读取 `test`，也不要在插件内部重新切分或偷偷合并
valid。需要 early stopping 的模型应显式读取 `train` 与 `valid`，仍不得以 test 指标选择参数。

## 7. 研究边界与常见失败

- `dataset-verify` 失败：先修复或重新生成数据发布，不要指向未注册目录绕过校验。
- 日期无数据：检查不可变版本的 `calendars/day.txt`、股票池和 feature 覆盖；不要自动把切分改成重叠。
- benchmark 缺失：`SH000300` 必须存在于本地数据；否则回测应失败，不能静默换基准。
- 模型结果弱：先视为研究证据；不要为改善结果改变已认证的数据、时序、成本或执行语义。
- 本例不是 walk-forward，也不是正式研究候选流程。需要受治理的 rolling OOS、身份与 lineage 时，使用
  仓库正式研究入口和 `configs/model_profiles/`，不要把本例产物直接升级或发布。

参考：Qlib 官方 [LightGBM + Alpha158 workflow](https://github.com/microsoft/qlib/blob/main/examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml)、
[workflow 配置与自定义模型集成](https://github.com/microsoft/qlib/blob/main/docs/start/integration.rst)。
