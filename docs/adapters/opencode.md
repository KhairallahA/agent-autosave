# OpenCode adapter

**Who it's for:** OpenCode users. The plugin follows OpenCode's plugin documentation and has not been run on a real install yet.

## At a glance

| | |
| --- | --- |
| **Captured** | Session created, idle (a turn ended) and compacted, and every tool call (`tool.execute.after`). |
| **Loaded** | OpenCode reads `AGENTS.md`, which points at `.autosave/SAVE.md`. A plugin cannot add context to a session, so nothing is injected. `agent-autosave resume opencode` runs `opencode --prompt "<block>"`. |
| **Status** | **From docs**: plugin API from <https://opencode.ai/docs/plugins/>; not run yet. |

## What init writes

OpenCode has no command hooks. `init --agents opencode` (or `add opencode`)
writes `.opencode/plugins/agent-autosave.js`, a small plugin that pipes each
event to `agent-autosave hook opencode <event>` and ignores every error, and
shares the `AGENTS.md` block. `remove opencode` deletes the plugin.

## Not confirmed yet

The event property names (`sessionID`, `info.id`) and a tool call's
arguments in `tool.execute.after` are not in the documentation; when they
are missing, the tool's name is recorded without its command.
[Run it once](../testing/hook-agents.md) to confirm.

## See also

- [Recording the hook agents](../testing/hook-agents.md)
- [Generic adapter](generic.md)
