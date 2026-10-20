# Configuration

**Who it's for:** Anyone who wants to change save limits, staleness, redaction or recording for one repo or all of them.

Read this when you want to change how much a save holds, when it is given
to a new session, or what is redacted. Most people never need to.

Settings are read from three layers, each overriding the one before:

1. the built-in defaults (below),
2. `~/.config/agent-autosave/config.json`, for every project,
3. `.autosave/config.json`, for this project (`init` writes the defaults
   there; `record` and `init --reconfigure` change it).

Reading never writes a file. An unknown field or a wrong type is an error
(`invalid_config`) that names the field, never its value. Each file only
needs the fields it changes:

```json
{"v": 1, "inject": {"stale_after_hours": 72}, "redact": {"extra_patterns": ["acme_[A-Za-z0-9]{32}"]}}
```

## Fields

| Field | Default | What it does |
| --- | --- | --- |
| `v` | `1` | Format version; must be 1. |
| `inject.on_status` | `["interrupted", "paused"]` | Save statuses that are given to a new session automatically. `active` and `done` saves are not, except when another session (any agent) left the task open. |
| `inject.max_chars` | `4000` | Longest block a new session is given (500 to 12000). Whole sections are dropped from the lowest priority up to fit; `show --context --explain` shows which. |
| `inject.resume_max_chars` | `8000` | Longest block `resume` and `show --context` use (500 to 12000). |
| `inject.stale_after_hours` | `24` | After this many hours the block and `status` say the save may be stale. |
| `inject.memory_on_start` | `true` | A session that is handed no unfinished task gets an `[agent-autosave project memory]` block: up to 5 accepted entries (pinned first) and the previous task. |
| `inject.memory_max_chars` | `800` | Longest project-memory block at session start (200 to 4000). |
| `capture.prompt_max_chars` | `2000` | Prompts are cut to this length before they are stored. |
| `capture.output_tail_lines` | `20` | Lines of a failing command's output kept. |
| `capture.ignore_tools` | `[]` | Tool names never recorded. |
| `capture.ignore_paths` | `["node_modules/", "dist/", ".git/", "__pycache__/", "*.pyc", ".DS_Store"]` | Files never listed as touched. |
| `compile.save_md_max_chars` | `12000` | Longest `SAVE.md` (256 to 16000). |
| `compile.last_actions` | `10` | Actions kept under "Where it stopped". |
| `compile.steering` | `5` | Recent user prompts kept as "User steering". |
| `compile.files` | `30` | Files kept under "Files and git". |
| `redact.extra_patterns` | `[]` | More regular expressions to mask, on top of the built-in secret patterns. |
| `redact.redact_env_values` | `true` | Also mask the values in the project's `.env` files. |
| `ledger.rotate_mb` | `5` | The ledger rotates into `history/` past this size. |
| `history.keep` | `50` | Archived files kept. |
| `budgets_ms.git_call` | `150` | Longest a git call may take in a hook before it is skipped. |
| `handoff.warn_at_percent` | `85` | When an agent's usage limit (Codex: the 5-hour window) reaches this share, its next prompt asks it once to record a handoff note (50 to 99). |
| `handoff.ask` | `true` | Ask for that handoff note at all. Usage is shown either way. |
| `record.enabled` | `false` | Keep raw hook payloads (set by `agent-autosave record on`). |
| `record.dir` | `""` | Where recordings go; empty means `.autosave/recordings/`. |
| `record.agent_version` | `""` | Stored in each recording. |
| `memory.max_entries` | `60` | `doctor` warns when `MEMORY.md` has more active entries than this. Nothing is deleted for you. |
| `recall.enabled` | `true` | Add possibly relevant memory entries and journal pages to a prompt. |
| `recall.max_items` | `3` | Most entries added to one prompt (1 to 10). |
| `recall.min_score` | `0.34` | Share of the prompt's terms an entry must match (above 0, at most 1). |
| `recall.max_chars` | `1200` | Longest recall block (200 to 4000). |
| `vault.path` | `""` | An Obsidian vault folder. Empty keeps the journal and `MEMORY.md` in `.autosave/`. |
| `vault.project` | `""` | This repo's folder inside `<vault>/agent-autosave/`. Written once, the first time the vault is used: the repo folder's name plus 4 hex characters. |
| `statusline.style` | `"plain"` | The Claude Code status line: `plain` or `emoji`. |
| `switch.order` | `[]` | Agents `go` and `resume --next` try, in order. Empty means the installed agents, in the order they were installed. |
| `integrations.agentic_stack` | `"auto"` | `auto` or `on`: work with agentic-stack when its `.agent/` folder is here (`on` also makes `doctor` warn when it is not). `off`: never touch `.agent/`. |

The wizard's preferences map onto two things: *Keep `.autosave/` out of
git* is the `.gitignore` line (`install.json` remembers if you said no, so
`add` does not add it later), and *Record raw hook payloads* is
`record.enabled`. *Where should the journal and project memory live?* sets
`vault.path`, and when agentic-stack is here, its question sets
`integrations.agentic_stack` to `auto` or `off`. When the wizard changes `config.json`, the old file is
kept as `config.json.bak`.

Fields that earlier versions wrote but nothing reads (`commit`,
`budgets_ms.hook`) are accepted and ignored, so old files keep loading.

## Recording on and off

Besides the wizard, `record` is the only command that changes `config.json`
(apart from `vault.project`, which is written once when the vault is first
used):

<!-- run: record-on -->
```console
$ agent-autosave record on
Recording on: raw hook payloads (secrets redacted, home as ~) go to ~/projects/hello/.autosave/recordings/<agent>/.
```
<!-- end run -->

And back:

<!-- run: record-off -->
```console
$ agent-autosave record off
Recording off.
```
<!-- end run -->

## See also

- [Security and privacy](security.md): what redaction covers
- [Local format](format.md): `config.json` among the other files
- [Contracts](contracts.md): `invalid_config` and its exit code
