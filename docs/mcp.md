# MCP server

**Who it's for:** Anyone whose agent supports MCP tools and wants it to read and write the save through tools instead of shell commands.

`agent-autosave mcp` is a local MCP server over stdio (JSON-RPC 2.0, the
standard library only, no network). Agents start it themselves; `init`
registers it with each agent unless you pass `--no-mcp`.

## Protocol

The server speaks MCP `2026-07-28` (stateless: every request carries
`_meta` with `io.modelcontextprotocol/protocolVersion` and
`clientCapabilities`; `server/discover` describes the server). For older
clients it also answers `initialize` for `2025-11-25`, `2025-06-18`,
`2025-03-26` and `2024-11-05`, and negotiates one of those. An unsupported
version gets error `-32022` with the supported list.

## Tools

| Tool | Does |
| --- | --- |
| `autosave_status` | Status, goal, plan progress, next step, failing command |
| `autosave_context` | The handoff block, as the next agent should read it |
| `autosave_note` | `type` (decision, todo, warning, tried, handoff), `text`, `keep` |
| `autosave_plan_set` | Replace the plan with `steps` |
| `autosave_plan_done` | Mark step `index` (from 1) done |
| `autosave_done` | Finish the task, with an optional `note` |
| `memory_search` | Search project memory and the journal for `query` |
| `memory_propose` | Propose a memory entry (`type`, `text`); a person reviews it |

There is no accept or reject tool: only you accept memory
(`agent-autosave memory review`). Each call is recorded like the matching
CLI command and credited to the agent named in the client's `clientInfo`.

## Resources

`autosave://save` is `SAVE.md`; `autosave://memory` is `MEMORY.md`.

## Where it is registered

| Agent | File | Entry |
| --- | --- | --- |
| Claude Code | `.mcp.json` | `mcpServers.agent-autosave` |
| Codex | `.codex/config.toml` | a `[mcp_servers.agent-autosave]` table between `# agent-autosave:start` and `# agent-autosave:end` (only with project hooks) |
| Cursor | `.cursor/mcp.json` | `mcpServers.agent-autosave`, with `--project ${workspaceFolder}` |
| Gemini CLI | `.gemini/settings.json` | `mcpServers.autosave` |

`doctor` checks each one; `remove` restores the files byte for byte.

## See also

- [Contracts](contracts.md)
- [Configuration](configuration.md)
- [Gemini CLI adapter](adapters/gemini.md)
