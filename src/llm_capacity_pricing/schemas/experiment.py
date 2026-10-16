"""M37：标准化压测实验清单（计划）。

本模块**只做实验规划**：把"要验证哪些配置"变成一份可执行清单 ——
每个实验点的部署身份、运行参数、负载条件、重复要求、必需指标，
以及可直接落地的 metadata 模板与命令骨架。

三条纪律：

* **不产生任何性能数值**：清单里只有参数与占位符，测量结果必须来自真实运行；
* **参数可追溯**：用到的每个命令行参数都记录它在仓库内归档文档中的出处，
  不凭印象拼命令；
* **清单即契约**：生成的 ``metadata_template`` 与现有适配器
  （``parse_vllm_bench_serve_json_full`` / ``parse_sglang_bench_serving_jsonl_full``）
  所需字段一一对应，照着填就能导入。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Framework = Literal["vllm", "sglang"]

#: 参数出处的归档文件（仓库内可查，含 sha256）
FLAG_SOURCES: dict[str, str] = {
    "vllm": "data/raw/official/vllm_bench_serve_2026-10-15.html",
    "sglang": "data/raw/official/sglang_bench_serving_guide_2026-10-15.html",
}

#: 必需指标（缺任一项都不能支撑 SLA 判定）
REQUIRED_METRICS: tuple[str, ...] = (
    "ttft_p99_ms",
    "tpot_p99_ms",
    "itl_p99_ms",
    "e2e_p99_ms",
    "output_throughput_tok_s",
    "request_throughput_rps",
    "error_rate",
)


class ExperimentGridPoint(BaseModel):
    """矩阵中的一行：一组负载条件。"""

    model_config = ConfigDict(extra="forbid")

    input_len: int
    output_len: int
    concurrencies: list[int]
    purpose: str

    @field_validator("input_len", "output_len")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("长度必须为正整数")
        return v

    @field_validator("concurrencies")
    @classmethod
    def _concurrencies(cls, v: list[int]) -> list[int]:
        if not v:
            raise ValueError("concurrencies 不得为空")
        if any(x < 1 for x in v):
            raise ValueError("concurrencies 必须为正整数")
        return v


#: 首轮推荐矩阵（计划 §三）。这是**测试任务**，不是已获得的性能结果。
DEFAULT_EXPERIMENT_GRID: tuple[ExperimentGridPoint, ...] = (
    ExperimentGridPoint(
        input_len=1024, output_len=256, concurrencies=[1, 8],
        purpose="建立低负载参考",
    ),
    ExperimentGridPoint(
        input_len=8192, output_len=256, concurrencies=[1, 8, 16, 32],
        purpose="验证常用上下文下的并发性能",
    ),
    ExperimentGridPoint(
        input_len=32768, output_len=256, concurrencies=[1, 8, 16],
        purpose="检查长上下文影响",
    ),
)


class ExperimentPoint(BaseModel):
    """一个实验点（一次压测要跑的参数组合）。"""

    model_config = ConfigDict(extra="forbid")

    point_id: str
    purpose: str

    # ---- 部署身份（必须与部署一致，否则采回来也匹配不上）----
    model_id: str
    model_name: str
    model_revision: str | None = None
    weight_precision: str | None = None
    kv_precision: str | None = None
    hardware_id: str
    hardware_model: str | None = None
    gpu_count: int
    tp_size: int
    dp_size: int
    nodes_required: int | None = None
    framework: Framework
    framework_version: str | None = None

    # ---- 负载条件 ----
    input_len: int
    output_len: int
    concurrency: int
    arrival_mode: Literal["unbounded", "request_rate"] = "unbounded"
    request_rate_rps: Decimal | None = None
    dataset_name: str = "random"
    #: 前缀缓存策略：清单里必须**显式**声明，避免"开了缓存/没开缓存"混在一起
    prefix_cache: Literal["disabled", "enabled"] = "disabled"

    # ---- 证据强度要求 ----
    warmup_requests: int | None = None
    duration_s: Decimal | None = None
    repeats: int = 2
    required_metrics: list[str] = Field(default_factory=lambda: list(REQUIRED_METRICS))

    # ---- 可执行材料 ----
    #: 服务端必须对齐的参数（**不给出启动命令**：启动参数与现场环境强相关，
    #: 且本仓库只归档了压测客户端文档，无法为其参数背书）
    server_args: dict[str, Any] = Field(default_factory=dict)
    #: 压测客户端命令骨架（每个参数都可在归档文档中查到，见 flag_sources）
    client_command: str
    #: **每次独立执行一份** metadata 模板（P0-1）：重复实验各自带独立 record_id，
    #: 否则同一实验点的多次运行会按 run_id 互相覆盖，拿不到重复观测的证据。
    metadata_templates: list[dict[str, Any]] = Field(default_factory=list)
    flag_sources: list[str] = Field(default_factory=list)

    # ---- 诚实性 ----
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _guard(self) -> "ExperimentPoint":
        if self.gpu_count != self.tp_size * self.dp_size:
            raise ValueError(
                f"gpu_count 必须等于 tp_size × dp_size（{self.tp_size}×{self.dp_size}）"
            )
        if self.arrival_mode == "request_rate" and self.request_rate_rps is None:
            raise ValueError("arrival_mode=request_rate 时必须给出 request_rate_rps")
        if self.repeats < 1:
            raise ValueError("repeats 至少为 1")
        if not self.required_metrics:
            raise ValueError("必须声明必需指标")
        if not self.metadata_templates:
            raise ValueError("必须给出 metadata 模板：否则采回来的数据无法导入")
        if len(self.metadata_templates) != self.repeats:
            raise ValueError(
                "metadata 模板数量必须等于 repeats（每次独立执行一份，"
                "否则多次运行会共用 run_id 互相覆盖）"
            )
        run_ids = [str(m.get("record_id")) for m in self.metadata_templates]
        if len(set(run_ids)) != len(run_ids):
            raise ValueError("同一实验点的各次执行必须使用不同的 record_id")
        return self


class BenchmarkExperimentPlan(BaseModel):
    """一份实验清单。**不含任何性能数值** —— 结果必须来自真实运行。"""

    model_config = ConfigDict(extra="forbid")

    plan_id: str
    #: 实验批次号：同一批次内 ID 稳定（重试幂等），跨批次各自独立保存
    batch_id: str = "unspecified"
    request_id: str | None = None
    points: list[ExperimentPoint] = Field(default_factory=list)

    #: 还缺哪些输入会让清单不完整（如目标 concoder 未给出）
    missing_inputs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _guard(self) -> "BenchmarkExperimentPlan":
        if not self.points:
            raise ValueError("实验清单不能为空")
        ids = [p.point_id for p in self.points]
        if len(ids) != len(set(ids)):
            raise ValueError("point_id 必须唯一")
        return self
