"""P1：AISimulate 模拟预测的数据契约。

**独立性是这个模块存在的理由**：模拟结果**不**复用任何实测字段。

* 不使用 ``BenchmarkRun`` / ``CapacityPoint`` / ``PerformanceProfile``；
* 不往 ``ttft_p99_ms`` / ``output_throughput_tok_s`` 这类实测字段里写数；
* 不引入新的实测 ``DataTrust`` 等级 —— ``simulated_prediction`` 只是**本对象内部**的标签，
  实测可信等级枚举（``schemas/performance.py`` 的 ``PRODUCTION_TRUST_LEVELS`` 那一套）保持不动。

因此"模拟结果污染实测结论"这件事在**结构上**就不可能发生，而不是靠约定。

两条纪律：

1. **口径**：AISimulate 扫描器给出的是**稳态单点**估计（实测约等于原生回放的中位数），
   官方 SLA 过滤用的是**聚合均值**；要判 P99 必须用原生回放。
   所以判定结果只有 ``predicted_*``，**永不**是 ``observed_*``。
2. **身份**：模型 + 精度 + system + 后端 + 后端版本 + 并行配置 + 负载 + 每步上下文预算
   必须齐备，缺一项就只能给 ``insufficient_prediction``。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

#: 模拟结论 —— 与实测状态（observed_pass / observed_fail）是**两套**取值，
#: 代码与报告里都不得互相转换。
SimulationVerdict = Literal[
    "predicted_pass",        # 预测 P99 满足用户阈值
    "predicted_fail",        # 预测 P99 超过用户阈值
    "insufficient_prediction",  # 预测缺失、样本不足或身份不完整
]

#: 领域状态（与 PlanResult.domains 的其它领域同一套取值）
SimulationStatus = Literal[
    "computed", "not_configured", "missing_data", "blocked", "error"
]

#: 部署模式
ServingMode = Literal["agg", "disagg", "unknown"]

#: 模拟数据的可信标签（**本对象内部**的标签，不是 BenchmarkRun 的 data_trust）
SIMULATION_TRUST_LABEL = "simulated_prediction"


class SimulationToolIdentity(BaseModel):
    """这次模拟是谁算的、用什么数据算的 —— 可复现性所必需。"""

    model_config = ConfigDict(extra="forbid")

    tool: str = "aisimulate"
    tool_version: str | None = None
    #: ``sweeper`` = ``aiconfigurator cli ...``；``native_replay`` = ``aisimulate predict``；
    #: ``mixed`` = 一次评估里两者都用上了
    surface: Literal["sweeper", "native_replay", "mixed", "unknown"] = "unknown"
    backend: str | None = None
    backend_version: str | None = None
    system: str | None = None
    database_mode: str | None = None
    #: 搜索/实验配置的归档文件与哈希（``exp_config.yaml``）
    search_space_file: str | None = None
    search_space_sha256: str | None = None
    #: 逐算子数据来源（``per_ops_source.json``）：silicon / mixed / empirical
    data_provenance: dict[str, str] = Field(default_factory=dict)
    #: 估算修正系数（例如 prefill_latency_correction / autoscale_ttft_correction_factor）。
    #: 这些是**启发式**，不是实测，必须随证据一起留存。
    correction_factors: dict[str, Decimal] = Field(default_factory=dict)


class SimulationWorkload(BaseModel):
    """模拟所用的负载 —— 与目标负载比对身份用。"""

    model_config = ConfigDict(extra="forbid")

    input_len: int | None = None
    output_len: int | None = None
    #: 扫描器把它记作 ``concurrency`` / ``bs``；原生回放记作 ``traffic.load``
    concurrency: int | None = None
    prefix: int | None = None
    #: **每步上下文 token 预算**。扫描器的 ``ctx_tokens`` 会改变 TTFT 达 70% 以上，
    #: 但它不在模型/硬件身份里 —— 不记录就会让两个"看起来一样"的配置互相矛盾。
    ctx_tokens: int | None = None
    #: 原生回放的流量停止条件（例如 ``requests=500``）；决定了 P99 的样本窗口
    stop_condition: str | None = None
    num_requests: int | None = None


class SimulationDeployment(BaseModel):
    """模拟推荐的部署身份。"""

    model_config = ConfigDict(extra="forbid")

    model_name: str | None = None
    model_precision: str | None = None
    system: str | None = None
    #: 该候选**自己**的后端与后端版本（扫描器 CSV 有 backend/version 列）。
    #: 不能用一个全局身份覆盖多后端结果 —— 那会让不同版本的结果看起来可比较。
    backend: str | None = None
    backend_version: str | None = None
    gpu_count: int | None = None
    serving_mode: ServingMode = "unknown"
    tp_size: int | None = None
    pp_size: int | None = None
    dp_size: int | None = None
    moe_tp_size: int | None = None
    moe_ep_size: int | None = None
    cp_size: int | None = None
    replicas: int | None = None
    gpus_per_worker: int | None = None
    #: 扫描器原样给出的并行串（如 ``tp8pp1dp1etp1ep1``），便于回溯
    parallel: str | None = None
    batch_size: int | None = None
    quant_modes: dict[str, str] = Field(default_factory=dict)
    memory_gib: Decimal | None = None
    balance_score: Decimal | None = None

    @property
    def parallelism_label(self) -> str:
        parts = [
            f"TP{self.tp_size}" if self.tp_size is not None else None,
            f"PP{self.pp_size}" if self.pp_size is not None else None,
            f"DP{self.dp_size}" if self.dp_size is not None else None,
        ]
        return " × ".join(p for p in parts if p) or "未声明"


class SimulationMetrics(BaseModel):
    """模拟指标 —— **一律带 ``sim_`` 前缀**，与实测字段物理隔离。

    缺失一律为 ``None``（不补造、不用均值顶替 P99）。
    """

    model_config = ConfigDict(extra="forbid")

    #: 时延：均值 / 中位数 / P99 分开存。**不得**用均值冒充 P99。
    sim_mean_ttft_ms: Decimal | None = None
    sim_median_ttft_ms: Decimal | None = None
    sim_p99_ttft_ms: Decimal | None = None
    sim_mean_tpot_ms: Decimal | None = None
    sim_median_tpot_ms: Decimal | None = None
    sim_p99_tpot_ms: Decimal | None = None
    sim_request_latency_ms: Decimal | None = None

    #: 吞吐
    sim_output_throughput_tok_s: Decimal | None = None
    sim_tokens_per_gpu: Decimal | None = None
    sim_tokens_per_user: Decimal | None = None
    sim_request_throughput_rps: Decimal | None = None

    #: 样本数：样本太少时 P99 无意义，必须与 P99 一起看
    sim_ttft_samples: int | None = None
    sim_tpot_samples: int | None = None
    sim_completed_requests: int | None = None


class SimulatedCost(BaseModel):
    """由**我们自己的** TCO 引擎算出的成本，挂在模拟候选旁边。

    刻意与工具输出分开：AISimulate 完全不涉及成本，这里的每个数都来自本仓库的
    TCO（复用 ``engines/tco.py`` 与既有的成本模板），因此**不是**模拟量。
    算不出来时保持 ``None`` + ``incomplete=True`` —— 绝不当作零成本。
    """

    model_config = ConfigDict(extra="forbid")

    hardware_id: str | None = None
    currency: str | None = None
    #: 币种是否已确认（未确认不得给预算结论，与既有预算规则一致）
    currency_confirmed: bool = False
    cost_basis: str | None = None
    horizon_years: int | None = None
    total_cash_over_horizon: Decimal | None = None
    capex_one_time: Decimal | None = None
    annual_cash_opex: Decimal | None = None
    annual_total_cost: Decimal | None = None
    incomplete: bool = True
    coverage_gaps: list[str] = Field(default_factory=list)
    #: 成本是谁算的（固定为本仓库的 TCO，便于报告里区分"模拟"与"成本"）
    source: str = "tco"


class SimulationCandidate(BaseModel):
    """一个模拟候选：部署身份 + 负载 + 指标 + 结论。"""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    deployment: SimulationDeployment
    workload: SimulationWorkload = Field(default_factory=SimulationWorkload)
    metrics: SimulationMetrics = Field(default_factory=SimulationMetrics)

    #: 由**我们自己的规则**算出：拿 ``sim_p99_*`` 与用户阈值比。
    #: 绝不采纳外部工具的均值型 SLA 过滤结论。
    verdict: SimulationVerdict = "insufficient_prediction"
    #: 判定依据（面向用户，说明为什么是这个 verdict）
    verdict_reasons: list[str] = Field(default_factory=list)
    #: 该候选能否与目标需求直接比较；不能时给出原因
    comparable: bool = False
    comparability_reasons: list[str] = Field(default_factory=list)
    #: 解析期的来源备注（例如"精度由量化列推断"）—— 与可比性原因分开存，不被覆盖
    notes: list[str] = Field(default_factory=list)

    # ---- 资源与预算门槛（R1.2 / R2.3）----
    #: 本仓库 Sizing 是否确认该并行配置**静态可行**。
    #: ``None`` = 未校验（不能进正式推荐池）；``False`` = 明确不可行。
    resource_feasible: bool | None = None
    resource_reasons: list[str] = Field(default_factory=list)
    #: 该配置在我们 Sizing 口径下的卡数（可能与工具报的不同，需一致才推荐）
    sizing_gpu_count: int | None = None
    #: 是否在用户给出的 GPU 上限内；``None`` = 用户没给上限
    within_gpu_budget: bool | None = None
    #: 预算比对：within / over / unknown / not_configured
    budget_status: str | None = None
    budget_reasons: list[str] = Field(default_factory=list)
    #: 原始证据（保存的输出文件），用于回溯到具体一行
    source_file: str | None = None
    source_sha256: str | None = None
    source_row: int | None = None
    #: 该配置的**成本**（来自本仓库的 TCO，不是模拟量）；未算出则 None
    cost: SimulatedCost | None = None


class SimulationResult(BaseModel):
    """一次模拟的整体结果。"""

    model_config = ConfigDict(extra="forbid")

    status: SimulationStatus = "not_configured"
    #: 本对象内部的可信标签；**不是** BenchmarkRun 的 data_trust
    data_trust: str = SIMULATION_TRUST_LABEL
    tool: SimulationToolIdentity = Field(default_factory=SimulationToolIdentity)
    candidates: list[SimulationCandidate] = Field(default_factory=list)
    #: 本次模拟相对目标需求的限制（口径、精度、覆盖、身份缺失）
    limitations: list[str] = Field(default_factory=list)
    #: 出错/缺数据时的可读说明
    message: str | None = None
    #: 读取的归档目录（离线模式）
    source_dir: str | None = None

    def judged(self) -> list[SimulationCandidate]:
        """可比、且**已经能给出 P99 结论**的候选（pass 或 fail 都算）。

        既不 compar，又没有 P99 的候选不进这个池子 —— 它们不参与任何排名。
        （扫描器候选天然属于后者：它不给 P99。）
        """
        return [
            c
            for c in self.candidates
            if c.comparable and c.verdict in ("predicted_pass", "predicted_fail")
        ]

    def rejection_reasons(self, candidate: SimulationCandidate) -> list[str]:
        """这个候选为什么不能进**正式推荐池**（空表示可以进）。"""
        reasons: list[str] = []
        if not candidate.comparable:
            reasons.extend(candidate.comparability_reasons or ["与目标不可比较"])
        if candidate.verdict not in ("predicted_pass", "predicted_fail"):
            reasons.extend(
                candidate.verdict_reasons or ["没有可用的 P99 预测结论"]
            )
        if candidate.resource_feasible is not True or candidate.within_gpu_budget is False:
            reasons.extend(
                candidate.resource_reasons
                or ["未通过本仓库 Sizing 的静态可行性校验（或拓扑本系统不建模）"]
            )
        if candidate.budget_status == "exceeds_budget":
            reasons.extend(candidate.budget_reasons or ["超出预算"])
        return reasons

    def eligible(self) -> list[SimulationCandidate]:
        """**正式推荐池**：身份明确 + 资源可行 + P99 完整 + 不超预算。

        这是 R1.3 的门槛。``judged()`` 只表示"有 P99 结论"，进不了推荐池。
        """
        return [c for c in self.candidates if not self.rejection_reasons(c)]

    def recommended(self) -> SimulationCandidate | None:
        """**严格推荐**：推荐池里预测为满足阈值、吞吐最高者。

        没有候选达标时返回 ``None`` —— 报告必须如实说明，不硬凑"最优"。
        """
        pool = [
            c
            for c in self.eligible()
            if c.verdict == "predicted_pass"
            and c.metrics.sim_output_throughput_tok_s is not None
        ]
        if not pool:
            return None
        return max(pool, key=lambda c: c.metrics.sim_output_throughput_tok_s or Decimal(0))

    def best_effort(self) -> SimulationCandidate | None:
        """**未达标参考配置**：推荐池里即使不达标也吞吐最高的那个。

        用于回答"在现有模型/硬件下最好能到哪一步、还差多少"。
        它的 ``verdict`` 多半是 ``predicted_fail``，报告**不得**称其为"推荐配置"。
        """
        pool = [
            c for c in self.eligible() if c.metrics.sim_output_throughput_tok_s is not None
        ]
        if not pool:
            return None
        return max(pool, key=lambda c: c.metrics.sim_output_throughput_tok_s or Decimal(0))
