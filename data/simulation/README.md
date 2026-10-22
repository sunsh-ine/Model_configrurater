# AISimulate 证据归档

本目录只存放**性能预测证据**，用于 `scripts/build_performance_coverage.py`
生成覆盖矩阵，以及离线 Demo 按工作点选择原生 P99。

## 内容

```
archives/
  <run_id>/
    manifest.json          # 工具版本、spec、输入/输出及 sha256、错误
    archive.json           # 本次归档复制了哪些文件
    exp_config.yaml        # 后端版本、数据库模式（SILICON）等身份
    predict/predict_1.yaml # 原生回放输入（部署 + 负载）
    predict/out_1/prediction.json
    search_input.yaml      # 自动搜索模式才有
    search/**/pareto.csv   # 扫描器稳态单点（没有 P99）
```

## 边界

* **不含价格、成本、收入或报价**；这些数据在本地 `data/exports/`、
  `data/private/`，不入库。
* 原生回放结果只写入 `sim_*` 语义；**不是实测**，不会写入
  `BenchmarkRun` / `CapacityPoint`，也不会产生 `observed_pass`。
* 扫描器 `pareto.csv` 只有稳态单点（均值口径），不能用于 P99 SLA 判定。
* 不同工作点（模型/精度/硬件/TP/PP/DP/ISL/OSL/并发/ctx）不得互相插值。

## 新增归档

```bash
# 单次原生回放
.venv/bin/python scripts/archive_simulation_evidence.py \
  --name z-live-c8-tp8-isl8192-osl256-bf16 \
  --predict-config /path/to/predict.yaml \
  --prediction /path/to/prediction.json \
  --precision bf16 --database-mode SILICON

# 自动搜索工作目录（manifest + 搜索 + 回放）
.venv/bin/python scripts/archive_simulation_evidence.py \
  --name z-live-pipeline-c32-tp4-bf16 \
  --work-dir data/exports/<run>/sim_work

# 重新生成覆盖矩阵
.venv/bin/python scripts/build_performance_coverage.py
```
