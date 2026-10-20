# N6-1 环境检查报告（只读）

**目标机器**：`compute01`（`10.0.102.61`，RTX 3090 24GB）
**检查时间**：2026-10-20 11:38–11:42（本地时间）
**执行方式**：**只读** —— 只运行了 `nvidia-smi`、`df`、`ps`、`docker ps`、
`pip list`、`find`、`curl -o /dev/null`（连通性探测）。
**未做任何变更**：没有安装软件、没有下载模型、没有写文件、没有停止/重启任何进程。

> ⚠️ 结论先行：**当前具备执行条件，但需要你批准 3 件事**（见 §6）。
> 在此之前 N6-2/N6-3 不会启动。

---

## 1. 硬件与系统

| 项 | 值 |
|---|---|
| 主机名 | `compute01` |
| 内核 | Linux 5.15.0-163-generic x86_64 |
| 运行时 | 已连续运行 99 天，load average 0.02（空闲） |
| GPU | 1× NVIDIA GeForce RTX 3090，24576 MiB |
| 显存占用 | 已用 445 MiB / 空闲 **23691 MiB**，利用率 0% |
| 驱动 / CUDA | 570.124.04 / **CUDA 12.8** |
| CPU / 内存 | 16 核 / 15 GiB（可用约 12 GiB） |

## 2. 磁盘

| 挂载点 | 容量 | 可用 | 使用率 |
|---|---|---|---|
| `/`（根分区） | 98 GB | **17 GB** | 83% |
| `/boot` | 2.0 GB | 1.7 GB | 18% |
| **`/user-storage`（网络存储）** | **185 TB** | **157 TB** | 16% |

> 根分区仍然紧张（与上次盘点一致），但 `/user-storage` 有 157 TB 可用且**可写**。
> 因此安装目录与模型缓存应放在 `/user-storage`，**不占根分区**。

## 3. 软件环境

| 项 | 值 |
|---|---|
| Python | 3.10.12（`/usr/bin/python3`），`venv` 模块可用 |
| 已装框架 | `torch 2.11.0+cu128`、`triton 3.6.0` |
| **未装** | **vLLM、SGLang**（`command -v` 均无） |
| conda | 无 |
| 容器 | Docker 可用；CDI 已注册 `nvidia.com/gpu=all`（容器内可用 GPU） |
| 正在运行的容器 | `yolo-demo-direct-app-1`（healthy，GPU 应用）、`yolo-demo-direct-proxy-1`、`alloy`、`dcgm-exporter` |
| 网络 | PyPI **可达**（200）；**HuggingFace 不可达**（超时） |
| HF 镜像/代理变量 | 未配置 |

## 4. 关键发现（会直接影响 N6 的做法）

1. **我们在这个机器上是 `root`。** 这是共享机器（有健康检查容器与监控），
   任何变更都要按"高影响操作"对待；本次检查全程只读。
2. **GPU 已被一个长期运行的服务占用**：PID 564209，用户 `lxd`，
   命令是 `gunicorn deploy.wsgi:app`（已运行 63 天），占用 436 MiB 显存。
   另有 `yolo-demo` 容器在跑。
   → 我们的测试**必须**与之共存：只用剩余 23 GB 显存，不停止、不重启任何既有进程。
3. **HuggingFace 不可达** → 不能现场下载模型。必须使用**本机已有权重**或内网镜像。
4. **本机已有可用模型**：
   `/user-storage/b20251112154056248bnsrgl/Llama-3.2-3B-Instruct`
   —— 12 GB（2 个 safetensors 分片），`LlamaForCausalLM`、28 层、hidden 3072、
   **bfloat16**、`max_position_embeddings` 131072；`config.json` / `tokenizer.json`
   / `tokenizer_config.json` / `generation_config.json` 齐全。
   3B bf16 权重约 6 GB，3090 的 23 GB 余量足够。
5. **PyPI 可达** → vLLM 可以通过 pip 装（约 2–4 GB 依赖）。
   系统已装 `torch 2.11.0+cu128`，因此建议用
   `python3 -m venv --system-site-packages` **复用已装的 torch**，避免重复下载和版本冲突风险。

## 5. N6-2 最小真机测试方案（**待批准，尚未执行**）

### 5.1 选型

| 项 | 选择 | 理由 |
|---|---|---|
| 模型 | **Llama-3.2-3B-Instruct（本机已有）** | HF 不可达；该权重完整、bf16、3B 可放进 3090 |
| 框架 | **vLLM**（单一框架） | PyPI 可达；只需装一套；SGLang 不装 |
| 运行时 | `venv --system-site-packages` | 复用已装 torch，减少安装体积与风险 |
| 安装位置 | `/user-storage/<新目录>/n6-vllm` | 不占 17 GB 的根分区 |
| 模型缓存 | 不下载，直接 `--model <本机路径>` | HF 不可达 |
| 端口 | 高端口（如 `127.0.0.1:18100`） | 避免与既有服务（8000）冲突 |
| 显存上限 | `--gpu-memory-utilization 0.5`（约 12 GB） | 给既有服务留足余量 |

> 注：任务书原示例是 `Qwen3-0.6B`，但**HF 不可达**、本机也没有该权重，
> 因此改用本机已有的 Llama-3.2-3B-Instruct。这不影响 N6 的目的
> —— 验证的是"真实 Benchmark 能否进入系统"，与具体模型无关。
> 模型精度/规模会作为实测记录的**身份**字段如实记录。

### 5.2 步骤（获得批准后执行）

1. 在 `/user-storage/<新目录>` 建 venv，装 vLLM（只装一套框架）；
2. 用 `--model /user-storage/.../Llama-3.2-3B-Instruct --gpu-memory-utilization 0.5
   --max-model-len 8192 --port 18100` 启动服务；
3. 小请求打通：一条 chat completion，确认服务与 tokenizer 正常；
4. `plan-benchmarks` 生成本轮压测清单与 metadata 模板（并发 1 / 2 / 4，ISL/OSL 取小值）；
5. 用 vLLM 自带 `bench serve` 跑低并发压测，保存**原始 JSONL** 与服务端启动参数；
6. `import-performance` 导入 → `check-import` 检查指标口径与一致性；
7. 逐项核对原始文件 sha256、成功数、TTFT/TPOT/ITL/E2E 的百分位定义；
8. 人工核实来源后 `attest-run`；
9. `validate-site` 得到该测试配置下的实测评价。

### 5.3 清理策略（先写清再做）

* venv、模型软链接、日志、压测产物统一放在 `/user-storage/<新目录>/` 下，
  **全部**在该目录内，不散落到 `/root`、`/tmp` 或根分区；
* 不修改系统 Python、不写 `/etc`、不改动 Docker 与既有容器；
* 结束时停止我们自己的服务进程，保留原始压测文件供复核，
  并给出删除命令（由你决定何时删）。

## 6. 需要你批准的 3 件事

1. **在该机器上安装 vLLM**（写入 `/user-storage/<新目录>`，用独立 venv，
   不改系统 Python）—— 这是外部写操作，需要明确授权；
2. **使用本机的 Llama-3.2-3B-Instruct 权重**（不再下载模型；
   请确认其使用符合内部规定）；
3. **在 GPU 上与既有服务共存运行**：我们占用上限 12 GB 显存、
   使用 18100 端口、不停止任何既有进程 —— 请确认这个边界可接受。

## 7. 明确不做

* 不把 3090 的实测结果外推为 H100 的性能，也不用于校准 AISimulate 的 P99 预测；
* 不修改本仓库的 Sizing / TCO / 门禁逻辑（除非 N6 发现字段映射缺陷，届时只做最小修复 + 回归测试）；
* 不处理未经授权的 Git 历史重写；生产报价保持禁用。
