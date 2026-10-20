# Changelog

All notable changes are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## 0.1.0 — unreleased

The first release ([release notes](docs/releases/v0.1.0.md)). Your coding
agent autosaves to `.autosave/` as it works. When it stops (a usage limit,
a crash, a closed terminal), the next session starts from the save, in the
same agent or another one.

### Added

- **Saves from the agent's own hooks.** Claude Code and Codex are built from
  recorded sessions, and so is Antigravity. Cursor, Gemini CLI and GitHub
  Copilot CLI hooks, and an OpenCode plugin, follow each agent's
  documentation. Any other agent that reads `AGENTS.md` gets an instruction
  block.
- **What is recorded:** the goal and later requests, the plan, commands and
  their exit codes, the failing command and its last output lines, touched
  files, the agent's last message and its usage.
  - A pipe that hides an exit (`… | tail`), or an agent that reports none, is
    shown as "exit unknown", never as a pass.
  - A test runner's failure summary marks the run as failing.
- **A deterministic save.** A compiler with no model turns the event ledger
  into `save.json` and `SAVE.md`, and the same events always give the same
  bytes. The format is specified as version 1, with JSON Schemas
  ([save format](docs/spec/save-format-v1.md)).
- **Handoffs.**
  - The save is given to the next session at start.
  - `agent-autosave resume <agent>` and `resume --next` start another agent
    with it.
  - `agent-autosave go claude-code codex` switches agents at a usage limit
    with one key.
  - Near a limit, the agent is asked for a handoff note first.
  - A crashed session's task is taken over by the next one.
  - Each git branch keeps its own task.
- **Tasks.** Run `agent-autosave done` to finish a task. `status`, `show`,
  `history`, `watch` and Claude Code's status line show where a task stands.
  Every command prints JSON with `--json` or when piped
  ([contracts](docs/contracts.md)).
- **Project memory.**
  - Notes from you and the agents become candidates. You accept them with
    `memory review`.
  - `MEMORY.md` is given to later sessions and recalled on matching prompts.
  - Each finished task gets a journal page.
  - Memory can live in an Obsidian vault and sits alongside agentic-stack.
- **An MCP server** (`agent-autosave mcp`) for the save, the plan, notes and
  memory.
- **Teams.** `share on` commits project memory and the journal.
  `handoff export` and `handoff import` pass one task to a teammate.
- **Setup you can undo.**
  - The `init` wizard shows every change before it writes anything.
  - `doctor` gives the fix for each problem.
  - `upgrade` refreshes what was installed.
  - `remove` restores every file byte for byte.
- **Installs** with Homebrew, pipx, `install.sh` or (experimental)
  `install.ps1`.

### Security

- Nothing leaves the machine: no network calls, no telemetry.
- Secrets are redacted before anything is written.
- `.autosave/` is kept out of git by default.
- Hooks always exit 0 and stay under 300 ms
  ([budgets](docs/contracts.md#performance-budgets)).
