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

## 2. 先做一次增量验证（推荐）

先做缩短验证周期的冒烟验证，命令如下（示例 `20250101-20260805`）：

```bash
tq --config configs/pipeline.yaml backfill --start 20250101 --end 20260805
tq --config configs/pipeline.yaml curate --start 20250101 --end 20260805
tq --config configs/pipeline.yaml stage-full --force
tq --config configs/pipeline.yaml dump-full
```

若上述步骤通过，再执行全量构建（与日常首次一致）：

## 2. 首次全量构建

```bash
tq --config configs/pipeline.yaml init-metadata
tq --config configs/pipeline.yaml backfill --start 20160101 --end 20260804
tq --config configs/pipeline.yaml curate
tq --config configs/pipeline.yaml stage-full --force
tq --config configs/pipeline.yaml dump-full
```

## 4. 训练、回测和选股

YAML 工作流：

```bash
export QLIB_DATA_URI=/absolute/path/to/data/qlib/cn_tushare_v1
qrun configs/workflow_lightgbm.yaml
```

或 Python 一体化流程：

```bash
tq --config configs/pipeline.yaml train-select
```

选股结果位于 `data/output/selection_YYYYMMDD.csv`。

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
- `dump_update` 仅用于在现有字段集合上追加新交易日；新增字段或修复历史数据应构建新版本数据集。
