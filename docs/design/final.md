# Design: the v0.1.0 feature set

**Who it's for:** contributors reviewing or extending the parts that finish v0.1.0: the MCP server, the status line and `watch`, the one-key switch (`go`), the extra hook agents, team sharing, the handoff benchmark and the frozen save format. For each part, this page gives the file formats, the commands, the config keys and the tests that prove it.

Everything stays standard library only, offline and `"v": 1`. Every new
file agent-autosave merges into a config is recorded in
`.autosave/install.json`, and `agent-autosave remove` restores it byte for
byte. Vendor facts come from official docs, cited next to the code that uses
them; anything a doc does not confirm sits behind detection that does
nothing when it is absent.

## Test isolation

`tests/conftest.py` has one autouse fixture that every test runs under. It
points `HOME` at a temporary folder, resets `PATH` to the directories that
hold the test interpreter and the system tools (`/usr/bin`, `/bin`), sets
`XDG_CONFIG_HOME` and `CODEX_HOME` inside that home, and clears the agent
markers (`CLAUDECODE`, `CLAUDE_CODE_CHILD_SESSION`, `CODEX_TOOL_CALL_ID`,
`CURSOR_AGENT`, `GEMINI_CLI`, `COPILOT_CLI`) and every `AGENT_AUTOSAVE_*`
variable. A test that needs one of them sets it itself.

`tests/test_isolation.py` runs a sample of the suite in a subprocess with all
of those variables set to junk values (a home with agent folders in it, a
`PATH` with fake `claude`, `codex` and `agent-autosave` binaries that fail,
and every marker set) and expects it to pass.

## MCP server

`agent-autosave mcp` speaks MCP over stdio: newline-delimited JSON-RPC 2.0
on stdin and stdout, nothing else on stdout, logs to `.autosave/errors.log`,
exit 0 when stdin closes.

**Protocol versions.** The current MCP specification is `2026-07-28`
(<https://modelcontextprotocol.io/specification/2026-07-28>). It is
stateless: there is no `initialize`; every request carries
`io.modelcontextprotocol/protocolVersion` in `_meta`, and `server/discover`
returns the supported versions. Clients released before it still open with
`initialize` (`2025-11-25` and earlier). The server answers both:

| Client sends | Server does |
|---|---|
| `server/discover` | lists `supportedVersions`, capabilities, `serverInfo` and instructions |
| a request with `_meta` protocol version `2026-07-28` | answers it; a missing `clientCapabilities` is `-32602` |
| a request with an unknown `_meta` protocol version | `-32022` with `data.supported` and `data.requested` |
| `initialize` | negotiates: echoes a supported requested version (`2025-11-25`, `2025-06-18`, `2025-03-26`, `2024-11-05`), else offers `2025-11-25` |
| `ping` | `{}` |

Results for a `2026-07-28` request carry `resultType: "complete"`, list and
read results carry `ttlMs: 0` and `cacheScope: "private"` (the save changes
on every event), and `_meta` names the server.

**Tools** (listed in this order; `tools/call` failures inside a tool are a
result with `isError: true`, unknown tools and bad arguments are `-32602`):

| Tool | Arguments | Same as |
|---|---|---|
| `autosave_status` | none | `agent-autosave status --json` (the save) |
| `autosave_context` | none | `agent-autosave context` |
| `autosave_note` | `type` (`decision`, `next`, `handoff`, `gotcha`, ...), `text`, `keep` | `agent-autosave note` |
| `autosave_plan_set` | `steps` (list of strings) | `agent-autosave plan set` |
| `autosave_plan_done` | `index` (1-based) | `agent-autosave plan done` |
| `autosave_done` | `note` | `agent-autosave done` |
| `memory_search` | `query` | `agent-autosave memory search` |
| `memory_propose` | `type`, `text` | `agent-autosave memory propose` |

No tool can accept or reject memory: that stays with a person
(`agent-autosave memory review`). Tool calls are recorded exactly like the
CLI calls they mirror. The note's `by` is the agent named in `clientInfo`
when it maps to one we know (`claude-code`, `codex`, `cursor`,
`gemini-cli-mcp-client`, ...), else `cli`. That is the only thing
`clientInfo` changes; the MCP spec asks servers not to change behaviour on it.

**Resources:** `autosave://save` (`SAVE.md`, `text/markdown`) and
`autosave://memory` (`MEMORY.md`). An unknown URI is `-32602`.

**Registration** (one entry per agent, `"feature": "mcp"` in the adapter
manifest, recorded in the receipt, removed byte for byte):

| Agent | File | Policy | Docs |
|---|---|---|---|
| Claude Code | `.mcp.json` `mcpServers.agent-autosave` | `merge_json` | <https://code.claude.com/docs/en/mcp> |
| Codex | `.codex/config.toml`, block between `# agent-autosave:start` and `# agent-autosave:end` | `managed_block` | <https://developers.openai.com/codex/mcp> |
| Cursor | `.cursor/mcp.json` `mcpServers.agent-autosave` | `merge_json` | <https://cursor.com/docs/context/mcp> |
| Gemini CLI | `.gemini/settings.json` `mcpServers.autosave` | `merge_json` | <https://geminicli.com/docs/tools/mcp-server/> |

Gemini's server is named `autosave`: Gemini's docs ask for names without
underscores and build tool names as `mcp_<server>_<tool>`. The Codex block
is skipped when `config.toml` already defines `[mcp_servers.agent-autosave]`.
`init` registers the server by default (`--no-mcp` skips it), the wizard asks
(default yes), `add` follows the receipt, `doctor` checks each entry and
`remove` takes it out. Instruction blocks say: "If the agent-autosave tools
are available, use them; otherwise run the commands."

## Status line and watch

`agent-autosave statusline` reads Claude Code's status line JSON on stdin
(<https://code.claude.com/docs/en/statusline>) and prints one line:

```
saved 4s ago | plan 2/5 | failing: pytest | 5h 72% | ctx 41%
```

Fields: `context_window.used_percentage`, `rate_limits.five_hour` and
`rate_limits.seven_day` (`used_percentage`, `resets_at`). Missing fields are
left out. `statusline.style` is `plain` (default) or `emoji`. When the
five-hour, weekly or context reading moved 5 points or more since the last
recorded one (state in `.autosave/statusline.json`), it appends a
`claude-code` `usage` event, so the save knows the battery level without a
transcript. It never fails: any error prints nothing and exits 0. Budget:
under 50 ms, measured in `tests/test_performance.py`.

**Install.** When no settings layer (`.claude/settings.local.json`,
`.claude/settings.json`, `~/.claude/settings.json`) has a `statusLine`,
`init` merges ours into `.claude/settings.json`. When one exists, the wizard
offers "wrap": ours runs theirs and appends our segment. The original
command is copied to `.autosave/statusline.json`; if it was in
`.claude/settings.json`, the receipt keeps its exact bytes and `remove`
restores them. Without the wizard, an existing status line is left alone.

**Codex** has no custom status line command: `tui.status_line` takes a list
of built-in item ids only
(<https://developers.openai.com/codex/config-reference>). Nothing is added.

`agent-autosave watch` redraws every 2 s: every branch slot, the task status,
usage per agent, the last action, the failing command and the pending memory
candidates. `q` quits. It uses `curses` when available, else a plain redraw;
`--once` prints one frame and exits (used by the docs and tests).

## The one-key switch

`agent-autosave go [agent ...]` starts the first agent with the terminal
handed straight to it (no stdin interception): with the resume block as its
first prompt when there is an unfinished task, otherwise with no prompt at
all. When it exits, `go` reads the save:

- the task is done: the chain ends;
- the agent stopped on a usage limit or overload (the save's `interrupted`),
  or its last usage reading was at or above 98%: `go` prints
  "Claude Code hit its usage limit at 14:02. Press Enter to continue in
  Codex, or q to stop." Enter runs the same path as `resume codex`, and the
  chain continues from there;
- anything else (the person quit): `go` says how to continue and stops.

`switch.order` (config) lists agents to switch through; by default the
launchable agents wired in this repo, in install order. `go` with no agents
uses it. `--auto` continues after a 5 s countdown that any key cancels.
`resume --next` resumes in the agent after the one that last worked on the
task.

## More hook agents

| Agent | Hooks file | Events | Docs | Status |
|---|---|---|---|---|
| Gemini CLI | `.gemini/settings.json` `hooks` | SessionStart, SessionEnd, BeforeAgent, AfterAgent, AfterTool, PreCompress | <https://geminicli.com/docs/hooks/reference/> | from docs |
| Antigravity | `.agents/hooks.json`, entry `agent-autosave` | SessionStart, PostToolUse, Stop | <https://antigravity.google/docs/hooks/> | from docs |
| Copilot CLI | `.github/hooks/agent-autosave.json` | sessionStart, sessionEnd, userPromptSubmitted, postToolUse, postToolUseFailure, agentStop, preCompact, errorOccurred | <https://docs.github.com/en/copilot/reference/hooks-reference> | from docs |
| OpenCode | `.opencode/plugins/agent-autosave.js` | session.created, session.idle, session.compacted, session.error, tool.execute.after | <https://opencode.ai/docs/plugins/> | from docs |

Each has a normalizer (`agent_autosave/normalize/<agent>.py`), install,
remove and doctor checks, and recording steps in `docs/testing/`. Antigravity
and Copilot payloads have no `hook_event_name`; the event comes from the
command line. The OpenCode plugin builds its own small payload (session id,
event type, directory, tool, arguments) and pipes it to
`agent-autosave hook opencode <event>`.

Copilot CLI also runs the hooks in `.claude/settings.json`. Claude Code's
payloads never carry `timestamp`; Copilot's do, so a `claude-code` payload
with `timestamp` is dropped (like Cursor's `cursor_version` import) and
`doctor` explains it.

## Team sharing

`agent-autosave share on` (in-repo mode only) re-scans `MEMORY.md` and the
journal with the redactor and refuses with the findings if anything looks
like a secret. Otherwise it turns the `.gitignore` block into
`.autosave/*`, `!.autosave/MEMORY.md`, `!.autosave/journal/` and adds a
`.gitattributes` block with `MEMORY.md merge=union`. `share off` puts the
`.gitignore` block back; the `.gitattributes` block stays (it is harmless
while nothing is tracked) until `remove` takes it out. Both are recorded in
the receipt, so `remove` stays byte exact.

`agent-autosave handoff export` writes `.autosave-handoff.md`: frontmatter
(`agent-autosave-handoff: 1`, `task_id`, `branch`, `status`, `goal`,
`exported`), the save as Markdown, and, after an
`<!-- agent-autosave:events -->` marker, the task's redacted ledger events,
one per line. `handoff import` starts a new task on the current branch
(which must be the file's branch) and replays those events into it, so the
compiler rebuilds the same save and the next session start hands it off as
usual. Events already rotated out of the ledger are not exported.

## Handoff benchmark

`bench/handoff/` holds 10 scripted tasks. Each generates a throwaway git repo
with an acceptance test and an interruption point K (tool events). The
runner starts agent A, kills its process group after K recorded tool events,
then starts agent B twice: with autosave, and as a control with the
instruction blocks removed and no injected context (hooks stay installed
but record silently: `inject.on_status` empty, no memory at start, recall
and the handoff ask off, agentic-stack off, no MCP server or status line,
so both arms are measured from their ledgers),
each prompted only "Continue the task in this repo." Metrics: finished (the
acceptance test passes), redone work (B's edits to files A had finished and B's re-runs of commands that already
passed), wall time and tokens (from the agent's transcript when it has
them). Results go to `bench/results/<date>.md` and `.json`. `--fake` uses a
scripted agent so the runner is tested offline; the README shows numbers
only from a real run.

## The frozen format

`docs/spec/save-format-v1.md` specifies the ledger, the save, the task slots,
the memory files and the handoff file. `schemas/` holds the final JSON
schemas; `tests/spec/` checks every golden, example and older fixture
against them. Additive changes stay `"v": 1`; a breaking change becomes
`"v": 2` with a migration that reads v1.

## Tests

| Part | Tests |
|---|---|
| Isolation | `tests/test_isolation.py` |
| MCP server | `tests/test_mcp.py` |
| MCP registration | `tests/test_mcp_install.py` |
| Status line | `tests/test_statusline.py` |
| watch | `tests/test_watch.py` |
| go | `tests/test_go.py` |
| Gemini, Antigravity, Copilot, OpenCode | `tests/test_normalize_gemini.py`, `tests/test_normalize_antigravity.py`, `tests/test_normalize_copilot.py`, `tests/test_normalize_opencode.py`, `tests/test_hook_agents.py` |
| Sharing and handoff files | `tests/test_share.py`, `tests/test_handoff_file.py` |
| Benchmark | `tests/test_bench.py` |
| Format | `tests/spec/` |
| Performance | `tests/test_performance.py` |

## See also

- [Continuity design](continuity.md): the save, slots and handoffs these build on
- [Memory design](memory.md): the journal, memory and recall
- [Architecture](../architecture.md): how the pieces fit
