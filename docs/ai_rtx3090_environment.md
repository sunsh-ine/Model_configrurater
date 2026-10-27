# AI1 RTX 3090 环境调查与部署

基线：`fd539e6`（AI0 整改后进入实测）
主机：`10.0.102.61`（compute01），SSH 免密，已获准执行。

## 1. 硬件与环境（实测）

| 项 | 值 |
|---|---|
| GPU | NVIDIA GeForce RTX 3090 |
| 显存 | 24576 MiB（24 GiB） |
| Compute Capability | 8.6（Ampere） |
| Driver | 570.124.04 |
| CUDA Runtime（驱动支持） | 12.8 |
| CPU | Intel Xeon Gold 6330 @ 2.00GHz，16 vCPU |
| 系统内存 | 15 GiB |
| OS | Ubuntu 22.04.4 LTS |
| 执行时占用 | 445 MiB（既有 gunicorn 进程，未干扰） |
| 空闲显存 | 约 23.7 GiB |

## 2. 框架选择

* 首选 SGLang：未安装，且需要额外验证 SM86 kernel 兼容性；
* 实际采用 **vLLM 0.11.0 + torch 2.8.0+cu128 + transformers 4.57.1**，
  独立 venv：`/user-storage/ai-bench/venv128`；
* 曾尝试 vLLM 0.31.0（cu13），因驱动仅支持 CUDA 12.8 无法初始化 CUDA，
  已弃用；该失败记录保留在 `install.log`；
* 模型使用本机权重 `/user-storage/b20251021174752115jsiyqm/Qwen3-8B`，
  16 GiB BF16，未联网下载。

## 3. 服务启动参数

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 VLLM_NO_USAGE_STATS=1 \
./venv128/bin/python -m vllm.entrypoints.openai.api_server \
  --model /user-storage/b20251021174752115jsiyqm/Qwen3-8B \
  --served-model-name Qwen/Qwen3-8B \
  --dtype bfloat16 --tensor-parallel-size 1 \
  --max-model-len 8448 --gpu-memory-utilization 0.90 \
  --max-num-seqs 8 --enforce-eager \
  --host 127.0.0.1 --port 18000 --trust-remote-code
```

说明：

* 端口 `18000`，避开既有 gunicorn 的 `127.0.0.1:8000`；
* `max-model-len=8448` 允许 8192 输入 + 256 输出；
* `--enforce-eager` 关闭 CUDA Graph，降低显存峰值；该部署身份随结果归档；
* 未使用量化，保持 BF16。

## 4. 显存与并发

Qwen3-8B BF16 权重约 16 GiB；启动后显存占用约 22.4 GiB，剩余约 1.7 GiB。
KV Cache 由 vLLM 按 `gpu-memory-utilization` 预分配，实际可并发以服务端
可分配 KV Token 与实测为准，不按理论值推算。

## 5. 结论

RTX 3090 成功运行 Qwen3-8B BF16 并返回流式结果；该环境只用于 3090 实测，
不能用于证明 H100/H200 的模拟预测准确。
