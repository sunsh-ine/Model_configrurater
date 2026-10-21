import { tool } from "@opencode-ai/plugin"
import { execFile } from "node:child_process"
import { promisify } from "node:util"
import { join } from "node:path"

const execFileAsync = promisify(execFile)
const ROOT = process.env.LCP_ROOT || process.cwd()
const PYTHON = process.env.LCP_PYTHON || join(ROOT, ".venv", "bin", "python")

async function runTool(args: string[], timeout = 120000): Promise<string> {
  try {
    const { stdout } = await execFileAsync(
      PYTHON,
      ["-m", "llm_capacity_pricing.agent.tool_cli", ...args],
      {
        cwd: ROOT,
        env: { ...process.env, PYTHONPATH: join(ROOT, "src") },
        timeout,
        maxBuffer: 16 * 1024 * 1024,
      },
    )
    return stdout.trim()
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    return JSON.stringify({
      status: "failed",
      needs_confirmation: false,
      error: `business tool failed: ${message}`,
    })
  }
}

export default tool({
  description:
    "Fetch the verified plan result and customer proposal for a run_id returned by evaluate_deployment_plan. Use only the returned facts.",
  args: {
    run_id: tool.schema.string().describe("run_id from evaluate_deployment_plan"),
  },
  async execute(args, context) {
    return await runTool([
      "report",
      "--run-id",
      args.run_id,
      "--session-id",
      context.sessionID,
    ])
  },
})
