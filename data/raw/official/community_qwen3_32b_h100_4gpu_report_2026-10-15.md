# Qwen3-32B Inference Benchmark: TPU v6e vs GPU H100 — Side-by-Side Comparison

## Executive Summary

This report compares **Qwen3-32B** inference performance on **Google Cloud TPU v6e-8 (Trillium)** vs **NVIDIA H100 80GB (A3 High)** using **vLLM** as the serving framework. Both benchmarks used identical workload parameters (31,400-token input, 128-token output, 200 prompts) for a fair apple-to-apple comparison.

**Key Finding:** The H100 GPU delivers **21% higher raw throughput**, but the TPU v6e is **69% more cost-efficient** (output tokens per dollar per hour).

---

## Hardware Comparison

| Specification | TPU v6e-8 (Trillium) | GPU H100 (A3 High 4g) |
|---|---|---|
| **Accelerator** | TPU v6e | NVIDIA H100 80GB HBM3 |
| **Number of Chips/GPUs** | 8 chips | 4 GPUs |
| **Memory per Chip/GPU** | 31.25 GiB HBM | 80 GiB HBM3 (81,559 MiB) |
| **Total Memory** | 249.97 GiB | 326.24 GiB |
| **Memory Used by Model** | 61.06 GiB | 15.27 GiB per GPU (61.08 GiB total) |
| **Memory for KV Cache** | 183.91 GiB | 54.77 GiB per GPU (219.08 GiB total) |
| **KV Cache Capacity** | 753,280 tokens | 897,312 tokens |
| **Max Concurrency at 32K** | 22.99x | 27.38x |
| **Zone** | europe-west4-a | us-east4-b |
| **VM Type** | Spot | Spot |
| **Machine Type** | TPU v6e-8 (topology 2x4) | a3-highgpu-4g |

---

## Software Comparison

| Component | TPU v6e | GPU H100 |
|---|---|---|
| **vLLM Version** | 0.13.0 | 0.17.0 |
| **Docker Image** | vllm/vllm-tpu:latest | vllm/vllm-openai:latest |
| **Runtime** | v2-alpha-tpuv6e | CUDA 13.0 / Driver 580.126.09 |
| **Model Precision** | bfloat16 | bfloat16 |
| **KV Cache Precision** | fp8_e5m2 (auto) | auto |
| **Tensor Parallelism** | 8 | 4 |
| **Chunked Prefill** | Enabled | Enabled |
| **CUDA Graphs** | N/A (TPU precompilation) | Disabled (--enforce-eager) |
| **Max Model Length** | 32,768 | 32,768 |
| **Max Num Batched Tokens** | 2,048 | 2,048 |
| **Max Num Seqs** | 256 | 256 |
| **GPU Memory Utilization** | 0.98 | 0.90 |

---

## Benchmark Configuration (Identical for Both)

| Parameter | Value |
|---|---|
| **Model** | Qwen/Qwen3-32B |
| **Backend** | vllm |
| **Dataset** | random |
| **Number of Prompts** | 200 |
| **Input Length** | 31,400 tokens |
| **Output Length** | 128 tokens |
| **Request Rate** | ∞ (inf) |
| **Seed** | 100 |

---

## Performance Results — Head-to-Head

### Overall Throughput

| Metric | TPU v6e-8 | GPU H100×4 | GPU Advantage |
|---|---|---|---|
| **Benchmark Duration (s)** | 444.63 | 366.39 | **17.6% faster** |
| **Successful Requests** | 200 | 200 | Tie |
| **Failed Requests** | 0 | 0 | Tie |
| **Request Throughput (req/s)** | 0.45 | 0.55 | **22.2% higher** |
| **Output Token Throughput (tok/s)** | 57.58 | 69.87 | **21.3% higher** |
| **Peak Output Throughput (tok/s)** | 269.00 | 260.00 | TPU 3.5% higher |
| **Total Token Throughput (tok/s)** | 14,181.79 | 17,210.03 | **21.3% higher** |

### Latency Metrics

| Metric | TPU v6e-8 | GPU H100×4 | GPU Advantage |
|---|---|---|---|
| **Mean TTFT (ms)** | 221,157.94 | 184,728.97 | **16.5% lower** |
| **Median TTFT (ms)** | 221,143.85 | 184,847.35 | **16.4% lower** |
| **P99 TTFT (ms)** | 437,746.44 | 359,749.43 | **17.8% lower** |
| **Mean TPOT (ms)** | 140.08 | 114.77 | **18.1% lower** |
| **Median TPOT (ms)** | 142.98 | 115.74 | **19.1% lower** |
| **P99 TPOT (ms)** | 143.47 | 154.03 | TPU 7.4% lower |
| **Mean ITL (ms)** | 140.08 | 114.77 | **18.1% lower** |
| **Median ITL (ms)** | 138.96 | 116.43 | **16.2% lower** |
| **P99 ITL (ms)** | 207.16 | 145.09 | **30.0% lower** |

### Latency Consistency

| Metric | TPU v6e-8 | GPU H100×4 |
|---|---|---|
| **TPOT P99/Mean ratio** | 1.024 (2.4% variance) | 1.342 (34.2% variance) |
| **ITL P99/Mean ratio** | 1.479 (47.9% variance) | 1.264 (26.4% variance) |

---

## Pricing & Cost-Efficiency Comparison

### Per-Hour Costs

| Pricing | TPU v6e-8 | GPU H100×4 |
|---|---|---|
| **On-Demand Price per Chip/GPU** | $2.70/chip/hr | $11.06/GPU/hr |
| **Number of Chips/GPUs** | 8 | 4 |
| **Total On-Demand Cost per Hour** | **$21.60/hr** | **$44.24/hr** |
| **Cost Ratio** | 1.0x | **2.05x more expensive** |

### Cost per Benchmark Run

| Metric | TPU v6e-8 | GPU H100×4 |
|---|---|---|
| **Benchmark Duration** | 444.63s (7.41 min) | 366.39s (6.11 min) |
| **Cost for This Run** | ~$2.67 | ~$4.51 |

### Cost Efficiency (Price-Performance)

| Metric | TPU v6e-8 | GPU H100×4 | TPU Advantage |
|---|---|---|---|
| **Output tok/s per $/hr** | **2.67** | 1.58 | **TPU 69% better** |
| **Total tok/s per $/hr** | **656.56** | 388.99 | **TPU 69% better** |
| **Requests per $/hr** | **20.83** | 12.43 | **TPU 68% better** |
| **Cost per 1M Output Tokens** | **$104.30** | $175.92 | **TPU 41% cheaper** |
| **Cost per Request** | **$0.0133** | **$0.0226** | **TPU 41% cheaper** |

---

## Key Insights

### 1. Raw Performance: GPU Wins by ~21%
The H100 GPU delivers 21% higher output token throughput (69.87 vs 57.58 tok/s) and 18% lower per-token latency (114.77ms vs 140.08ms TPOT). This is despite using **half the number of accelerators** (4 GPUs vs 8 TPU chips).

### 2. Cost Efficiency: TPU Wins by ~69%
Despite lower raw throughput, the TPU v6e is significantly more cost-efficient:
- **69% more output tokens per dollar** per hour
- **41% cheaper per request** at the workload level
- Total hourly cost is **less than half** ($21.60 vs $44.24)

### 3. Latency Consistency
- **TPU** has more consistent TPOT (P99/Mean = 1.024, only 2.4% variance)
- **GPU** has more consistent ITL tail latency (P99/Mean = 1.264 vs TPU's 1.479)
- Overall, TPU P99 TPOT is actually lower than GPU (143.47ms vs 154.03ms)

### 4. Memory Architecture Differences
- TPU uses fp8_e5m2 KV cache automatically, effectively doubling cache capacity
- GPU has more total VRAM (326 GiB vs 250 GiB) but uses auto KV cache dtype
- Both have sufficient capacity for 32K context with >20x concurrency

### 5. Software Stack Differences
- TPU used vLLM 0.13.0 (TPU-specific build)
- GPU used vLLM 0.17.0 (newer version with additional optimizations)
- GPU used `--enforce-eager` (no CUDA graphs) due to memory constraints; with CUDA graphs enabled, GPU performance would likely be higher

---

## Summary Table: Quick Reference

| | TPU v6e-8 | GPU H100×4 | Winner |
|---|---|---|---|
| **Output tok/s** | 57.58 | 69.87 | 🏆 GPU (+21%) |
| **Mean TPOT (ms)** | 140.08 | 114.77 | 🏆 GPU (-18%) |
| **P99 TPOT (ms)** | 143.47 | 154.03 | 🏆 TPU (-7%) |
| **Total tok/s** | 14,181.79 | 17,210.03 | 🏆 GPU (+21%) |
| **Cost/hour** | $21.60 | $44.24 | 🏆 TPU (-51%) |
| **Output tok/s per $/hr** | 2.67 | 1.58 | 🏆 TPU (+69%) |
| **Cost per 1M output tokens** | $104.30 | $175.92 | 🏆 TPU (-41%) |
| **KV Cache (tokens)** | 753,280 | 897,312 | 🏆 GPU (+19%) |

---

## Caveats & Fair Comparison Notes

1. **vLLM versions differ**: TPU used v0.13.0 vs GPU v0.17.0. Newer versions may have optimizations benefiting GPU.
2. **CUDA Graphs disabled on GPU**: `--enforce-eager` was used due to OOM during graph capture at 0.98 memory utilization. With CUDA graphs, GPU performance would be higher.
3. **Memory utilization**: TPU used 0.98 vs GPU 0.90 due to CUDA graph memory overhead.
4. **Different accelerator counts**: 8 TPU chips vs 4 H100 GPUs. The TPU needed 8 chips for sufficient memory; the GPU could fit the workload on 4 GPUs.
5. **Spot pricing**: Both used spot VMs. On-demand prices are used for cost calculations.
6. **KV cache dtype**: TPU auto-selects fp8_e5m2 for KV cache; GPU uses auto (likely fp16/bf16).

---

*Report generated: March 11, 2026*
*TPU Benchmark: Project <YOUR_GCP_PROJECT_ID>, europe-west4-a*
*GPU Benchmark: Project <YOUR_GPU_PROJECT_ID>, us-east4-b*
