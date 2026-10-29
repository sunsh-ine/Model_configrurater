"""AJ0.3：真实 Benchmark 实验预注册契约。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BenchmarkWorkpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workpoint_id: str
    input_len: int
    output_len: int
    concurrency: int | None = None
    request_rate_rps: Decimal | None = None
    expected_seeds: list[int]
    #: 预注册时绑定的运行目录名；用于把“同 seed 的探索运行”与正式重复区分开
    expected_run_ids: list[str] = Field(default_factory=list)
    requests_per_run: int = 500
    repeats: int = 5
    ttft_p99_ms_max: Decimal | None = None
    tpot_p99_ms_max: Decimal | None = None

    @model_validator(mode="after")
    def _guard(self) -> "BenchmarkWorkpoint":
        if (self.concurrency is None) == (self.request_rate_rps is None):
            raise ValueError("concurrency 与 request_rate_rps 必须且只能给出一个")
        if not self.expected_seeds:
            raise ValueError("expected_seeds 不得为空")
        if len(set(self.expected_seeds)) != len(self.expected_seeds):
            raise ValueError("expected_seeds 不得重复")
        if self.repeats != len(self.expected_seeds):
            raise ValueError("repeats 必须等于 expected_seeds 数量")
        if self.requests_per_run < 1:
            raise ValueError("requests_per_run 必须为正整数")
        return self


class BenchmarkExperimentManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    experiment_id: str
    created_at: datetime
    manifest_status: Literal["preregistered"] = "preregistered"
    runtime_config_sha256: str
    client_tool: str = "openai-compatible-stream-client/v1"
    server_framework: str = "vllm"
    server_framework_version: str = "0.11.0"
    warmup_requests: int = 0
    workpoints: list[BenchmarkWorkpoint] = Field(default_factory=list)

    @model_validator(mode="after")
    def _guard(self) -> "BenchmarkExperimentManifest":
        if not self.workpoints:
            raise ValueError("workpoints 不得为空")
        ids = [w.workpoint_id for w in self.workpoints]
        if len(ids) != len(set(ids)):
            raise ValueError("workpoint_id 必须唯一")
        return self

    def expected_run_count(self) -> int:
        return sum(len(w.expected_seeds) for w in self.workpoints)
