import { tool } from "@opencode-ai/plugin"
import { execFile } from "node:child_process"
import { promisify } from "node:util"
import { join } from "node:path"

const execFileAsync = promisify(execFile)
const ROOT = process.env.LCP_ROOT || process.cwd()
const PYTHON = process.env.LCP_PYTHON || join(ROOT, ".venv", "bin", "python")

async function runTool(args: string[], timeout = 180000): Promise<string> {
  try {
    const { stdout } = await execFileAsync(
      PYTHON,
      ["-m", "llm_capacity_pricing.agent.tool_cli", ...args],
      {
        cwd: ROOT,
        env: { ...process.env, PYTHONPATH: join(ROOT, "src") },
        timeout,
        maxBuffer: 8 * 1024 * 1024,
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
    "List the model, GPU and server IDs supported by this deployment planner. Call this first to confirm the user's model and hardware exist.",
  args: {},
  async execute() {
    return await runTool(["catalog"])
  },
})
