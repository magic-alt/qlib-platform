# 本地 Qlib 数据回测：从数据到自定义机器学习算法

这是仓库保留的 **qrun / workflow YAML 教学案例**。它适合学习 Qlib 原生 `DatasetH -> Model -> SignalRecord -> SigAnaRecord -> TopkDropoutStrategy` 主链路，以及验证自定义 Qlib `Model` 插件。

如果目标是从现有 `data/` 一路完成 DatasetVersion 检查、Alpha158 Market/Daily/PIT、多模型比较、prediction-only portfolio backtest 和 walk-forward，请优先使用 [`docs/local_research_quickstart.md`](../../docs/local_research_quickstart.md) 中的 `tq-research` 入口。本目录不会复制那套研究编排逻辑。

```text
不可变本地 DatasetVersion
    -> Alpha158 特征
    -> 模型训练
    -> test 段预测
    -> SignalRecord / SigAnaRecord
    -> TopkDropout 模拟回测
```

与 Qlib 官方 CSI300 示例相比，本案例保留 Qlib workflow 的核心结构，但使用本仓库的 `TushareAlpha158Fundamental`、动态 A 股股票池、涨跌停字段、成交量约束、100 股整手和次日开盘成交。它只生成本地研究证据，不提交真实订单、不访问 final holdout、不创建正式候选，也不发布 `TARGET_PORTFOLIO`。

## 1. 文件说明

| 文件 | 用途 |
| --- | --- |
| `run_backtest.py` | 跨平台主入口；校验 DatasetVersion、绑定 `QLIB_DATA_URI`、校验 qrun contract 并执行 workflow |
| `run_backtest.ps1` | Windows PowerShell 兼容入口 |
| `run_backtest.sh` | macOS/Linux shell 入口，内部调用 `run_backtest.py` |
| `workflow_lightgbm.yaml` | 默认 LightGBM 案例 |
| `workflow_ridge.yaml` | Qlib Ridge 低复杂度基线 |
| `workflow_custom_ridge.yaml` | 当前目录自定义模型插件案例 |
| `custom_model.py` | 最小完整 `Model.fit/predict` 插件示例 |

三个 workflow 除模型段和实验名外，数据、标签、切分、策略、成本与成交假设保持一致，便于做单变量模型比较。

## 2. 前置条件

从仓库根目录执行，并始终使用仓库本地虚拟环境。

### Windows PowerShell

```powershell
$RepoPython = '.\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $RepoPython)) {
    throw '缺少 .\.venv\Scripts\python.exe，请先创建仓库本地环境。'
}
& $RepoPython -m pip install -c constraints\ci.txt -e '.[dev]'
```

### macOS / Linux

```bash
RepoPython=.venv/bin/python
[ -x "$RepoPython" ] || { echo 'missing .venv/bin/python' >&2; exit 1; }
$RepoPython -m pip install -c constraints/ci.txt -e '.[dev]'
```

本案例默认使用 TuShare 开发 profile 下的 `research-current` DatasetVersion 引用。runner 会：

1. `dataset-resolve research-current`；
2. `dataset-verify research-current --mode deep`；
3. 将返回的不可变 DatasetVersion path 绑定给 `QLIB_DATA_URI`；
4. 执行 `validate-qrun-contract`；
5. 最后调用仓库本地 `qrun`。

因此不要把易变的 `data/qlib/current` 一类目录硬编码到 workflow。

仅检查数据而不训练：

### Windows

```powershell
$env:QLIB_REPO = '.'
$env:QLIB_DATA_URI = 'data/qlib'
& $RepoPython -m qlib_platform --config configs\pipeline_tushare_dev.yaml dataset-resolve research-current
& $RepoPython -m qlib_platform --config configs\pipeline_tushare_dev.yaml dataset-verify research-current --mode deep
```

### macOS / Linux

```bash
export QLIB_REPO=.
export QLIB_DATA_URI=data/qlib
$RepoPython -m qlib_platform --config configs/pipeline_tushare_dev.yaml dataset-resolve research-current
$RepoPython -m qlib_platform --config configs/pipeline_tushare_dev.yaml dataset-verify research-current --mode deep
```

这些环境变量只是让开发 profile 能加载；真正传给 qrun 的 provider path 由 runner 根据 DatasetVersion registry 解析结果重新绑定。

## 3. 运行 LightGBM 基线

### Windows PowerShell

```powershell
.\examples\local_qlib_backtest\run_backtest.ps1 -Model lightgbm
```

或者直接使用跨平台 Python runner：

```powershell
& $RepoPython examples\local_qlib_backtest\run_backtest.py --model lightgbm
```

### macOS / Linux

```bash
bash examples/local_qlib_backtest/run_backtest.sh --model lightgbm
```

或者：

```bash
$RepoPython examples/local_qlib_backtest/run_backtest.py --model lightgbm
```

明确指定数据引用和实验名：

```bash
$RepoPython examples/local_qlib_backtest/run_backtest.py \
  --model lightgbm \
  --dataset-ref research-current \
  --experiment-name local_alpha158_lgb_trial_01
```

runner 会输出最终绑定的 DatasetVersion ID、不可变数据路径、workflow 和实验名。

## 4. 固定研究区间

案例当前固定采用以下不重叠区间，并给 5 日标签留出隔离窗口：

| 段 | 日期 | 作用 |
| --- | --- | --- |
| train | 2018-10-01 至 2024-12-27 | 拟合模型和学习型处理器 |
| gap | 2024-12-28 至 2025-01-07 | 隔离未来收益标签前视窗口 |
| valid | 2025-01-08 至 2025-07-02 | early stopping / 参数判断 |
| gap | 2025-07-03 至 2025-07-10 | 隔离 valid 与 test |
| test / backtest | 2025-07-11 至 2026-08-10 | 样本外预测和模拟回测 |

不要因为本地数据更新就自动把 test 结束日向后顺延，再继续依据该区间调参。观察过的 OOS 结果已经不能重新当作未见数据。

## 5. 读取结果

qrun 的主要 artifact 位于配置的 recorder root 下，默认结构类似：

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

需要可审计的 Markdown、Parquet、图表和 PDF 时，可在找到 artifact 目录后执行：

```powershell
& $RepoPython scripts\export_qrun_backtest_report.py `
  --artifact-dir mlruns\examples_local_backtest\<experiment-id>\<run-id>\artifacts `
  --workflow-config examples\local_qlib_backtest\workflow_lightgbm.yaml `
  --output-dir data\output\local_backtest_report `
  --data-root data
```

研究解读顺序建议为：先看 `IC / Rank IC` 及稳定性，再看扣成本后的超额收益、信息比率、最大回撤，最后看成交填充率、持仓、现金、换手和模拟成交。正 IC 不等于扣费后盈利，组合盈利也不证明未见数据上的模型泛化。

## 6. 调整 LightGBM 参数

复制 workflow，只修改模型参数并使用新实验名：

```powershell
Copy-Item examples\local_qlib_backtest\workflow_lightgbm.yaml `
  examples\local_qlib_backtest\workflow_lightgbm_trial.yaml
```

常用参数：

| 参数 | 作用 | 建议 |
| --- | --- | --- |
| `learning_rate` / `num_boost_round` | 学习率与最大迭代数 | 降低学习率通常需要更多迭代，保留 early stopping |
| `num_leaves` / `max_depth` | 模型容量 | 同时增大容易过拟合 |
| `lambda_l1` / `lambda_l2` | 正则化 | 只根据 valid 选择 |
| `colsample_bytree` / `subsample` | 特征/样本采样 | 固定 seed 后再比较 |
| `num_threads` | CPU 并行度 | 不应改变研究定义 |

运行任意 workflow：

```bash
$RepoPython examples/local_qlib_backtest/run_backtest.py \
  --workflow examples/local_qlib_backtest/workflow_lightgbm_trial.yaml \
  --experiment-name local_alpha158_lgb_trial_02
```

同一轮模型比较不要同时改变 DatasetVersion、AlphaPack/handler、label、train/valid/test、成本或策略参数。

## 7. Ridge 与自定义 Qlib Model

Ridge 基线：

```bash
$RepoPython examples/local_qlib_backtest/run_backtest.py --model ridge
```

自定义 Ridge 插件：

```bash
$RepoPython examples/local_qlib_backtest/run_backtest.py --model custom_ridge
```

`custom_model.py` 展示了 Qlib 自定义算法最小契约：

- 继承 `qlib.model.base.Model`；
- `fit()` 只读取训练数据；
- 保存可序列化的拟合状态；
- `predict()` 返回保留 `datetime/instrument` MultiIndex 的预测；
- 对空训练集、非有限输入、列变化和非法参数 fail closed。

如果目的是开发仓库统一 ModelAdapter，而不是单个 qrun workflow 插件，请转到 [`docs/local_research_quickstart.md`](../../docs/local_research_quickstart.md) 的“Custom ModelAdapter”章节；正式研究 CLI 的模型族由 `src/qlib_platform/models/` registry 管理。

## 8. XGBoost / PyTorch 与完整模型矩阵

本 qrun 教学目录不再复制所有模型 profile。仓库正式模型比较使用 `tq-research`：

```bash
$RepoPython -m qlib_platform.research.research_quickstart matrix
```

默认比较：

```text
Alpha158 Market × Ridge / LightGBM / XGBoost
Alpha158 Daily  × Ridge / LightGBM / XGBoost
Alpha158 PIT    × Ridge / LightGBM / XGBoost
```

显式加入 PyTorch：

```bash
$RepoPython -m qlib_platform.research.research_quickstart matrix \
  --model ridge --model lightgbm --model xgboost --model pytorch
```

这样 XGBoost/PyTorch 参数、runtime probe、DatasetVersion、PredictionSnapshot 和 prediction-only portfolio backtest 仍走仓库统一 ModelAdapter/研究链路，而不是在 qrun 示例中维护第二套配置。

## 9. 研究边界与常见失败

- `dataset-verify` 失败：先修复/重新构建数据，不要指向未注册目录绕过校验。
- 日期无数据：检查 DatasetVersion 的 calendar、instrument universe 和 feature 覆盖，不要自动把 split 改成重叠。
- benchmark 缺失：`SH000300` 缺失时应 fail closed，不要静默替换基准。
- 模型结果弱：先把它当研究证据，不要为改善结果修改数据时序、成本或执行语义。
- 本例不是正式 walk-forward/candidate lifecycle。正式 rolling OOS 使用 `tq-research` 或底层 `research-run --mode walk-forward`。
- 当前治理状态始终以 [`docs/current_state.md`](../../docs/current_state.md) 为准；CLI/脚本存在不等于授权 final holdout、candidate selection 或 publishing。

参考上游：Qlib 官方 LightGBM + Alpha158 workflow 与 workflow/custom-model integration 文档。本文只描述本仓库维护的本地示例行为。
