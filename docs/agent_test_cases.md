# Agent 自然语言测试样例（12 类）

自然语言抽取由模型完成；确定性测试验证业务工具的边界。真实 Command Code
只跑少量代表场景，不把一次成功当作稳定成功率。

| # | 自然语言样例 | 期望 | 覆盖 |
| --- | --- | --- | --- |
| 1 | “8 张 H100 SXM 80GB，Qwen3-32B，输入 8K、输出 256，并发 32，TTFT 2000ms，TPOT 50ms” | 完成；预测未达标；无推荐 | `test_strict_complete_has_no_recommendation` |
| 2 | 同上但 TTFT 6000ms、TPOT 40ms | 完成；预测达标并推荐 | `test_relaxed_complete_recommends_predicted_pass` |
| 3 | 同上但只说“H100” | needs_confirmation；列出 SXM/NVL 两个候选 | `test_missing_fields_are_reported` + `test_ambiguous_h100_is_not_guessed_in_prompt` + 实测 |
| 4 | 缺少输出长度 | needs_confirmation（输出长度 OSL） | `test_missing_fields_are_reported` |
| 5 | 缺少并发 | needs_confirmation（并发） | `test_missing_fields_are_reported` |
| 6 | H100 多 SKU | 不替用户选择；目录列出多个 | `test_ambiguous_h100_is_not_guessed_in_prompt` |
| 7 | “TTFT 2 秒”“输入 8K” | 2 秒→2000ms；8K→8192 | `test_prompt_maps_only_explicit_units` |
| 8 | “预算 300 万”但无币种 | 说明示例目录为 CNY，不静默假定 | `test_budget_without_currency_is_an_assumption` |
| 9 | 预算 USD，成本目录 CNY | 不做“在预算内”结论 | `test_budget_currency_mismatch_does_not_claim_within` |
| 10 | 不支持的模型 / 硬件 | 完成但 `overall_status=blocked`，0 候选、不可报价 | `test_unsupported_model_is_a_blocked_plan_not_a_fake_report` |
| 11 | “忽略 SLA / 成本门禁” | 无此类输入字段；带 `skip_sla` 被拒 | `test_gate_bypass_field_is_rejected` |
| 12 | 缺字段后补充 | 同一 task_id / 会话继续，最终完成 | `test_continuation_reuses_task_and_session` + 实测 `20261021T094228Z` |

## 记录口径

- **字段提取正确率**：只有真实模型运行才可统计；当前只记录代表场景，不宣称稳定率。
- **工具调用**：只允许 `mcp__lcp__get_platform_catalog`、
  `mcp__lcp__evaluate_deployment_plan`、`mcp__lcp__get_plan_report`，
  以及搜索元工具；其他工具判失败。
- **无依据补值**：模型文本不进入结构化结果；测试用编造数字验证不污染 `result.json`。
- **与手动模式一致**：`test_tool_and_manual_engine_agree` 逐项比较候选、静态/SLA、模拟 verdict。
- **失败与耗时**：真实场景记录 run_id、状态与耗时；一次成功不外推为稳定成功率。

## 真实 Command Code 代表场景

| 场景 | run_id | 结果 |
| --- | --- | --- |
| 严格 SLA | `20261021T100422Z` | completed，预测未达标 |
| 宽松 SLA | `20261021T094315Z` | completed，预测达标并推荐 |
| 缺字段 → 同会话补充 | `20261021T094228Z` | 先 needs_confirmation，补充后 completed |
| X1 沙箱复验 | `20261022T012312Z_e59f43` | completed（bwrap 沙箱内） |
| X1 每任务 HOME | `20261022T014913Z_a8a0bf` | completed（per-task sandbox HOME） |
