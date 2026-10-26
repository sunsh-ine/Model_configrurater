"""AG1：实验计划与运行清单。

与 ``schemas/experiment.py``（M37 压测计划）不同：这里的清单描述**已冻结的
模拟重复实验**，用于判断“预期 seed 是否都跑了、是否都有可用的 P99”。

实验清单在运行前冻结；聚合时必须用它核对“预期跑了什么”，不能只统计
“恰好找到了什么”。缺失、失败、取消和成功都要留下记录。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ExperimentRunStatus = Literal[
    "succeeded", "failed", "cancelled", "missing", "invalid"
]
ExperimentCompleteness = Literal["complete", "incomplete", "invalid", "excluded"]


class ExperimentRun(BaseModel):
    """一次预期运行的执行记录；seed 是运行身份的一部分。"""

    model_config = ConfigDict(extra="forbid")

    seed: int
    status: ExperimentRunStatus
    archive: str | None = None
    archive_sha256: str | None = None
    config_file: str | None = None
    config_sha256: str | None = None
    #: 真正控制本次运行的 predict.yaml（含 traffic.seed/到达率/并行参数）
    predict_config_file: str | None = None
    predict_config_sha256: str | None = None
    #: 去掉允许变化字段后的标准化配置哈希
    normalized_config_sha256: str | None = None
    completed_requests: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    notes: list[str] = Field(default_factory=list)


class ExperimentManifest(BaseModel):
    """一个工作点、一组独立 seed 的冻结实验计划。"""

    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    created_at: datetime
    #: 预注册（运行前冻结）或历史证据重建；二者不能混为一谈
    manifest_status: Literal["preregistered", "reconstructed"] = "reconstructed"
    execution_status: Literal["planned", "running", "completed", "aborted"] = "completed"
    preregistered_at: datetime | None = None
    #: 标准化基础配置及其哈希；只允许 allowed_variation_fields 变化
    base_config: dict[str, Any] | None = None
    base_config_sha256: str | None = None
    allowed_variation_fields: list[str] = Field(default_factory=lambda: ["seed"])
    tool: str = "aisimulate"
    tool_version: str | None = None

    # ---- 冻结的部署身份 ----
    model_name: str
    model_precision: str | None = None
    system: str | None = None
    backend: str | None = None
    backend_version: str | None = None
    gpu_count: int | None = None
    serving_mode: str = "agg"
    tp_size: int | None = None
    pp_size: int | None = None
    dp_size: int | None = None
    replicas: int | None = None

    # ---- 冻结的负载身份 ----
    input_len: int
    output_len: int
    concurrency: int | None = None
    arrival_mode: str
    request_rate_rps: Decimal | None = None
    dataset: str | None = None
    trace_id: str | None = None
    num_requests: int | None = None
    stop_condition: str | None = None

    #: 重复性实验契约：每个 seed 至少要完成的请求数
    required_completed_requests: int = 500
    expected_seeds: list[int]
    #: 明确排除在本实验之外的已有 seed（必须写明；不能静默丢弃）
    out_of_scope_seeds: list[int] = Field(default_factory=list)
    config_file: str | None = None
    config_sha256: str | None = None
    runs: list[ExperimentRun] = Field(default_factory=list)

    @field_validator("expected_seeds")
    @classmethod
    def _seeds_non_empty_and_unique(cls, value: list[int]) -> list[int]:
        if not value:
            raise ValueError("expected_seeds 至少给出一个 seed")
        if len(set(value)) != len(value):
            raise ValueError("expected_seeds 不得重复")
        return sorted(value)

    @field_validator("required_completed_requests")
    @classmethod
    def _positive_required_requests(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("required_completed_requests 必须为正整数")
        return value

    @model_validator(mode="after")
    def _runs_cover_expected(self) -> "ExperimentManifest":
        if set(self.out_of_scope_seeds) & set(self.expected_seeds):
            raise ValueError("out_of_scope_seeds 不得与 expected_seeds 重叠")
        if len(set(self.out_of_scope_seeds)) != len(self.out_of_scope_seeds):
            raise ValueError("out_of_scope_seeds 不得重复")
        run_seeds = {run.seed for run in self.runs}
        if run_seeds - set(self.expected_seeds):
            raise ValueError("runs 中存在不在 expected_seeds 里的 seed")
        if len(run_seeds) != len(self.runs):
            raise ValueError("runs 中同一 seed 只能有一条记录")
        if self.arrival_mode == "fixed_concurrency" and self.concurrency is None:
            raise ValueError("固定并发实验必须声明 concurrency")
        if self.arrival_mode in ("poisson", "constant_rate") and self.request_rate_rps is None:
            raise ValueError("开放到达实验必须声明 request_rate_rps")
        if self.arrival_mode not in (
            "fixed_concurrency",
            "poisson",
            "constant_rate",
            "trace",
            "unbounded",
        ):
            raise ValueError(f"不支持的 arrival_mode={self.arrival_mode!r}")
        if self.arrival_mode == "trace" and not self.trace_id:
            raise ValueError("trace 实验必须声明 trace_id")
        if self.manifest_status == "preregistered":
            if self.preregistered_at is None:
                raise ValueError("预注册实验必须记录 preregistered_at")
            if self.base_config is None or not self.base_config_sha256:
                raise ValueError("预注册实验必须冻结 base_config 与哈希")
            if self.execution_status not in ("planned", "running", "completed"):
                raise ValueError("预注册实验的 execution_status 非法")
        if "seed" not in self.allowed_variation_fields:
            raise ValueError("allowed_variation_fields 必须包含 seed")
        config_hashes = {
            run.config_sha256 for run in self.runs if run.config_sha256
        }
        if len(config_hashes) > 1:
            raise ValueError("同一实验的各 run config_sha256 必须一致")
        if self.config_sha256 and config_hashes and config_hashes != {self.config_sha256}:
            raise ValueError("manifest.config_sha256 与 run 的配置哈希不一致")
        return self

    def identity_key(self) -> tuple[Any, ...]:
        """用于与模拟候选匹配的冻结身份（不含 seed）。"""
        return (
            self.model_name,
            self.model_precision,
            self.system,
            self.backend,
            self.backend_version,
            self.gpu_count,
            self.tp_size,
            self.pp_size,
            self.dp_size,
            self.input_len,
            self.output_len,
            self.concurrency,
            self.arrival_mode,
            str(self.request_rate_rps)
            if self.request_rate_rps is not None
            else None,
            self.dataset,
            self.trace_id,
            self.num_requests,
        )
