# AE1：负载身份契约

基线：`b4dd888`

## 1. 为什么要区分

同样是“ISL8192 / OSL256 / C32”，固定并发与 Poisson 到达不是同一负载：

* `fixed_concurrency`：闭环保持最多 N 个在途请求，完成后再补；
* `poisson`：按平均到达率产生请求，队列可能积累；
* `constant_rate`：固定间隔到达，同样可能排队。

把固定并发的 P99 用到 Poisson 请求上，会低估或高估尾延迟。AE 起把到达模式
作为工作点身份的一部分。

## 2. 契约字段

`SimulationWorkload` / `ArchiveEntry` / 覆盖矩阵证据现在包含：

| 字段 | 含义 |
|---|---|
| `arrival_mode` | `fixed_concurrency` / `poisson` / `constant_rate` / `trace` / `unknown` |
| `request_rate_rps` | 开放到达的平均请求率（req/s）；固定并发为空 |
| `concurrency` | 固定并发的在途上限；Poisson 为空 |
| `dataset` | 数据集身份；历史归档未记录时保持空 |
| `seed` | 随机种子（Poisson/constant-rate）；同到达率不同种子视为重复实验，不是新工作点 |
| `num_requests` / `stop_condition` | 有限预测窗口（当前 500 请求） |

`check_comparability()` 规则：

1. 到达模式必须双方都声明且一致；
2. `poisson`/`constant_rate` 的到达率必须一致；
3. 数据集双方都声明时必须一致；历史归档缺失时保留 unknown，并在候选 notes
   中提示，不据此否定已有工作点；
4. 固定并发的 `concurrency` 仍按原规则比较；
5. 不同到达模式、不同到达率、不同数据集不得合并为同一工作点。

## 3. 历史归档

* 原生输入 `traffic.load.type: concurrency` → `fixed_concurrency`；
* `poisson` / `constant_rate` → 对应模式，并读取 `requests_per_second` 与 `seed`；
* 缺失字段保持 `None`，不猜测；
* AD 的 61 个工作点仍为 `fixed_concurrency`，数值不变；
* AE 新增的 Poisson 证据与固定并发证据在覆盖矩阵中分开。

## 4. 数据库模式来源

`database_mode` 新增 `database_mode_source`：

* `tool_observed`：工具输出明确确认（当前 `aisimulate predict` 不提供）；
* `explicitly_configured`：输入配置显式指定；
* `archive_declared`：归档人员/脚本声明；
* `unknown`：没有证据。

当前归档脚本的 `--database-mode SILICON` 属于 `archive_declared`，
不是工具实测确认。能力探测矩阵不声明 SILICON。
