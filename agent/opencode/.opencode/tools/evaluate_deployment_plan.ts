import { tool } from "@opencode-ai/plugin"
import { spawn } from "node:child_process"
import { join } from "node:path"

const ROOT = process.env.LCP_ROOT || process.cwd()
const PYTHON = process.env.LCP_PYTHON || join(ROOT, ".venv", "bin", "python")

// 需求通过 stdin 传递：不落地临时文件，也不出现在进程参数里
function runToolWithInput(
  args: string[],
  input: unknown,
  timeout = 240000,
): Promise<string> {
  return new Promise((resolve) => {
    const child = spawn(
      PYTHON,
      ["-m", "llm_capacity_pricing.agent.tool_cli", ...args],
      {
        cwd: ROOT,
        env: { ...process.env, PYTHONPATH: join(ROOT, "src") },
        timeout,
      },
    )
    let stdout = ""
    let stderr = ""
    child.stdout.on("data", (chunk) => {
      stdout += String(chunk)
    })
    child.stderr.on("data", (chunk) => {
      stderr += String(chunk)
    })
    child.on("error", (error) => {
      resolve(
        JSON.stringify({
          status: "failed",
          needs_confirmation: false,
          error: `business tool failed: ${error.message}`,
        }),
      )
    })
    child.on("close", (code) => {
      const text = stdout.trim()
      if (code === 0 && text) {
        resolve(text)
        return
      }
      resolve(
        JSON.stringify({
          status: "failed",
          needs_confirmation: false,
          error: `business tool failed (exit ${code}): ${stderr.slice(0, 500)}`,
        }),
      )
    })
    child.stdin.write(JSON.stringify(input))
    child.stdin.end()
  })
}

export default tool({
  description:
    "Run the existing deployment planner with structured requirements. Returns a task contract with status (completed/needs_confirmation/failed), run_id, summary, conclusions and artifact paths. Never invent numbers; use its output.",
  args: {
    model_id: tool.schema.string().optional().describe("Model ID from get_platform_catalog, e.g. qwen3-32b"),
    hardware_id: tool.schema.string().optional().describe("GPU hardware ID, e.g. nvidia-h100-sxm-80gb"),
    server_id: tool.schema.string().optional().describe("Optional server/chassis ID"),
    gpu_count: tool.schema.number().optional().describe("Maximum number of GPUs available"),
    input_len: tool.schema.number().optional().describe("Input length (ISL, tokens)"),
    output_len: tool.schema.number().optional().describe("Output length (OSL, tokens)"),
    concurrency: tool.schema.number().optional().describe("Concurrent requests"),
    ttft_p99_ms_max: tool.schema.number().optional().describe("TTFT P99 upper bound in milliseconds"),
    tpot_p99_ms_max: tool.schema.number().optional().describe("TPOT P99 upper bound in milliseconds"),
    budget_amount: tool.schema.number().optional().describe("Budget upper bound in the cost catalog currency"),
    weight_precision: tool.schema.string().optional().describe("bf16 or fp8"),
    ranking: tool.schema.string().optional().describe("throughput or cost"),
  },
  async execute(args, context) {
    const payload = Object.fromEntries(
      Object.entries(args).filter(([, value]) => value !== undefined && value !== null),
    )
    return await runToolWithInput(
      ["evaluate", "--session-id", context.sessionID],
      payload,
    )
  },
})
