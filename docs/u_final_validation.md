# U 阶段：Demo 最终集成与交付验收

日期：2026-10-21
基线：`b40a637` + 本阶段改动
环境：Python 3.12.3 / Docker 29.5.0（compute01）/ 离线归档模拟

## 结论

推荐身份、CLI 机箱、工作点口径与部署文档已对齐；三场景在页面、CLI、JSON、
客户报告与技术报告中结论一致。全量 **927 passed, 1 skipped**；
`verify_all.sh` **66/66**。U5 实时 AISimulate 未执行（无独立环境），如实记为跳过。

## U1：推荐与入口正确性

### U1.1 模拟并行身份

原生回放输入为 `aggregated` 单 worker、`parallelism: {tensor: 8, replicas: 1}`，
预测中 `decode_gpus_per_worker=8`。现在显式推导并在候选 `notes` 中记录依据：

| 字段 | 结果 | 依据 |
| --- | --- | --- |
| TP | 8 | 输入 `tensor` |
| PP | 1 | 输入未声明 pipeline，且 `decode_gpus_per_worker == tensor` |
| DP | 1 | 单一 aggregated worker 组，`replicas=1` |
| GPU 总数 | 8 | `decode_gpus_per_worker × replicas` |

候选 ID 变为 `sim-native:…tp8pp1dp1-c32-n500`，方案/技术报告显示
`TP8×PP1×DP1`，不再出现 `PP—×DP—`。

反向用例：删除输入里的 `replicas` 后，DP 保持 `None`，并写明
“DP 未声明…不以 DP1 代替”；该候选 `eligible()` 为空，不能进入推荐池。
资源门槛不再使用 `dp_size or 1`。

### U1.2 CLI 机箱

`--server` 默认改为 `None`，由 `server_for_hardware()` 自动匹配。
回归用例：

- `--hardware nvidia-h200-sxm-141gb` → 请求 `server_id=hgx-h200-8gpu`；
- 显式 `--server hgx-h100-8gpu` → 原样保留（不会被自动匹配掩盖）。

### U1.3 ctx_tokens 工作点

`ctx_tokens=16384` 的扫描器候选仍可作为“相同请求负载下的配置比较”，
但候选备注与报告明确写出：它**不是**与原生回放（ctx=ISL=8192）相同的工具工作点。
`ctx_tokens=8192` 的候选没有这条提示。

## U2：三场景一致性

| 场景 | 页面 | CLI/JSON | 报告 |
| --- | --- | --- | --- |
| strict | 无达标推荐，显示未达标参考 | `predicted_fail`，`recommended=None` | 未达标参考与证据缺口说明 |
| relaxed | 蓝色预测达标，推荐 TP8×PP1×DP1 | `predicted_pass`，P99 数值不变 | 首行演示阈值说明 + 推荐 |
| cost | 成本明细、完整性、预算未知 | `ranking=cost`，排序被拒绝 | 降级原因与成本门禁说明 |

- 预测达标与实测达标仍严格分开：三个场景都没有 `observed_pass`；
- 报价发布始终为「不可发布」；
- 成本场景仍只验证门禁，不制造“成本最优”。

## U3：Docker Demo 交付

- 镜像 `llm-capacity-ui:v1.3`，非 root（UID 10001），`--memory 2g`，不使用 GPU；
- `streamlit==1.66.0` 已固定；基线镜像构建时解析为 Python 3.12.15；
- 运行产物挂载 `/opt/llm-capacity-ui/runs`，历史目录保留；
- `data/local.db`、`data/exports`、`data/private`、raw xlsx 均不进入镜像；
- 部署、更新、回滚见 `docs/deployment_compute01.md`；
- 未修改既有 nginx / yolo-demo；未配置公网映射。

## U4：端到端演示回归

1. 在部署环境选择三个预设并生成：strict / relaxed / cost 均成功；
2. relaxed 页面显示 `TP8×PP1×DP1`、蓝色预测达标与推导说明；
3. cost 页面显示一次性/年现金、全周期 `—`、完整性「不完整」、预算「未知」；
4. 成本优先提示“成本排序未生效，已降级为吞吐优先”；
5. 三个场景各生成方案、技术报告、JSON、演示说明；
6. 重新对同一目录运行 CLI 返回 2（不覆盖历史）；
7. 页面下载按钮与磁盘报告来自同一次 `PlanResult`；
8. `pytest` 924 passed / 1 skipped，`verify_all` 66/66。

截图在 `docs/ui/u-phase/`。

## U5：实时 AISimulate（未执行）

`AISIMULATE_PYTHON` 未设置，`verify_all.sh` 第 61 步明确跳过。
本记录**不能**作为实时搜索/回放已验证的证据；需要独立工具环境单独执行。

## 未做

- N5 真人易用性验收、N6 RTX 3090 真机 Benchmark；
- 新的性能算法、GPU 适配、Agent 需求解析；
- 公网映射与生产报价发布。
