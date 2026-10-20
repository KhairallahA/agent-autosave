// agent-autosave for OpenCode: pipes session and tool events to
// `agent-autosave hook opencode <event>`. Plugin API and event names from
// https://opencode.ai/docs/plugins/ . Fails open: errors are ignored.
import { spawn } from "node:child_process"

const COMMAND = "agent-autosave"

function send(event, payload) {
  try {
    const child = spawn(COMMAND, ["hook", "opencode", event], { stdio: ["pipe", "ignore", "ignore"] })
    child.on("error", () => {})
    child.stdin.end(JSON.stringify(payload))
  } catch (error) {}
}

function sessionOf(event) {
  const p = (event && event.properties) || {}
  return p.sessionID || (p.info && p.info.id) || ""
}

export const AgentAutosave = async ({ directory }) => ({
  event: async ({ event }) => {
    if (["session.created", "session.idle", "session.compacted"].includes(event.type)) {
      const session = sessionOf(event)
      if (session) send(event.type, { session_id: session, cwd: directory })
    }
  },
  "tool.execute.after": async (input, output) => {
    send("tool.execute.after", { session_id: input.sessionID || "", cwd: directory, tool: input.tool,
                                 args: (output && output.args) || input.args || {} })
  },
})
