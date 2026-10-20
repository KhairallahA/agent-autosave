# Gemini CLI adapter

**Who it's for:** Gemini CLI users. The hooks follow Gemini CLI's documentation and have not been recorded on a real install yet.

## At a glance

| | |
| --- | --- |
| **Captured** | Prompts (`BeforeAgent`), tool calls with their failures (`AfterTool`), turn ends (`AfterAgent`), compaction (`PreCompress`), session start and end. |
| **Loaded** | The save is given at `SessionStart` (`hookSpecificOutput.additionalContext`); recall is added to prompts at `BeforeAgent`. `agent-autosave resume gemini` runs `gemini --prompt-interactive`. |
| **MCP** | `.gemini/settings.json` gets an `mcpServers.autosave` entry (Gemini CLI does not allow underscores in server names). |
| **Status** | **From docs**: shapes from <https://geminicli.com/docs/hooks/reference/>; not recorded yet. |

## What init writes

`init` (when `gemini` is on PATH, or `--agents gemini`) and `add gemini`
merge six hooks into `.gemini/settings.json` (timeouts in milliseconds, as
Gemini CLI expects), plus the MCP server unless `--no-mcp`, and a short
managed block in `GEMINI.md`. Each hook is named `agent-autosave`. Gemini
CLI fingerprints project hooks, so it asks you once before running them.
`remove gemini` puts both files back byte for byte.

## Resume

`agent-autosave resume gemini` runs `gemini --prompt-interactive "<block>"`:
the resume block is the first prompt and the session stays interactive.
`--dry-run` prints the command. Exit 1 (`agent_not_installed`) if `gemini`
is not on PATH.

## Not confirmed yet

A shell command's exit code is not documented in `AfterTool`; a failing
command is one whose `tool_response.error` is set (exit 1 unless the text
says `Exit code N`). Usage limits are not documented in any hook, so `go`
switches away from Gemini CLI only on a usage reading another source
recorded. [Record real payloads](../testing/hook-agents.md) to confirm.

## When Gemini CLI is missing


Asking for an agent that is not installed stops before anything is written:

<!-- run: error-agent-missing -->
```console
$ agent-autosave init --agents gemini
error: Gemini CLI was not found on this machine.
hint: Install Gemini CLI, then run agent-autosave init again.
```
<!-- end run -->

## See also

- [Recording Gemini CLI and the other hook agents](../testing/hook-agents.md)
- [Antigravity adapter](antigravity.md)
- [MCP server](../mcp.md)
