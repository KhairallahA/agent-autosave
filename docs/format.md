# Local format

**Who it's for:** Anyone reading `.autosave/` by hand, or writing a tool that reads it.

Everything lives in `.autosave/` at the repository root, as UTF-8. JSON
files and event rows carry `v: 1`; `SAVE.md` starts with a version comment.
An unsupported version or a malformed row is an error, never a guess. The
frozen definition of every format, with its schema, is
[Save format, version 1](spec/save-format-v1.md).
Two real saves, with their ledgers, are in [examples/](../examples/README.md).

| File | What it holds |
| --- | --- |
| `events.jsonl` | The ledger: one normalized event per line, append only |
| `save.json` | The compiled save, the source of truth (sorted keys, trailing LF) |
| `SAVE.md` | The save as Markdown, fixed headings, at most 12,000 characters by default |
| `task.json` | One task per git branch: `{"v": 1, "current": branch, "slots": {branch: task}}`, each task with its id, when it started, the HEAD at start, its ledger cursor, and after a clear the previous task |
| `slots/<branch>-<hash>.json` | The parked save of each branch you are not on |
| `ledger.json` | The size at which the ledger rotates next |
| `config.json` | Project configuration (written by `init`; changed only by `record`, `init --reconfigure`, which keeps the old file as `config.json.bak`, and once by the vault, which records `vault.project`) |
| `install.json` | What `init` and `add` changed, so `remove` can undo it |
| `adapter-<agent>.json` | Per-session hook state: turn counters, plan-tool bookkeeping |
| `history/t_*.json` | Archived tasks |
| `history/events-*.jsonl` | Rotated ledger segments |
| `recordings/<agent>/` | Raw hook payloads, only while `record on` is set |
| `MEMORY.md` | Project memory: entries a person accepted, in `## Decisions`, `## Dead ends`, `## Gotchas`, `## Conventions` and `## Expired` (in the vault instead when `vault.path` is set) |
| `journal/YYYY/MM/*.md` | One page per finished task (in the vault instead when `vault.path` is set) |
| `memory/candidates.jsonl` | Notes proposed for `MEMORY.md`, each `{"v": 1, "id", "type", "text", "source", "status", ...}`; `status` is `pending`, `accepted` or `rejected` |
| `memory/index.json` | The recall index: terms of each entry, page and lesson; rebuilt from the files, safe to delete |
| `memory/state.json` | Which tasks have a journal page and were offered as candidates |
| `errors.log` | Redacted hook and command errors, capped at 1 MiB |
| `.writer.lock`, `.errors.lock`, `.capture.lock` | Lock files, never replaced |

`init` adds `.autosave/` to `.gitignore` in a managed block, so none of this
is committed (unless you tell the wizard you manage `.gitignore` yourself).

Task ids look like `t_<UTC date>_<time with microseconds>`, with a numeric
suffix when an archive already has that name. History keeps the newest 50
files (`history.keep`); files it did not write are left alone.

The ledger rotates at 5 MiB (`ledger.rotate_mb`). Events of every branch's
task stay in the live file, so a long task can grow past the limit; the next
threshold then moves up by another 5 MiB.

Configuration is read from the built-in defaults, then
`~/.config/agent-autosave/config.json`, then `.autosave/config.json`; reading
never writes either file. Unknown fields are an error. Every field and its
default is in [configuration](configuration.md).

## Branch slots

Each git branch has its own task. The branch is read from `.git/HEAD` (a
worktree's `.git` file is followed) on every event, without running git.
When it changes, the current save is parked as `slots/<branch>.json`,
`task.json` is written with the new `current` branch, and that branch's
parked save comes back, or a new task starts if it has none. A detached HEAD
keeps the current slot; outside git the slot is `""`. The parked file name
is the branch with other characters replaced by `_`, plus the first 8 hex
digits of its SHA-256, so `feature/csv` and `feature_csv` never collide
(`_none.json` outside git).

A `task.json` written before slots (a single task object) still loads, as the
slot of the branch checked out now; the next event rewrites it in the new
shape in one atomic replace. `status --all` lists every slot, and
`show --task <id>` and `resume <agent> --task <id>` read any slot or archived
task.

## Memory files

`MEMORY.md` is the source of truth and you may edit it. Each entry is one
bullet: `- text <!-- m:{"type":"decision","source":{...},"added":"2026-10-12","until":null,"replaces":null} --> ^m-3fa2c1`.
The `^m-` block id comes last, where Obsidian looks for it, and lets
Obsidian and journal pages link to the entry (`[[MEMORY#^m-3fa2c1]]`); the
comment carries the metadata. Bullets in the older order (id before the
comment) are still read and are rewritten in this order at the next write. Parsing and
writing back gives the same bytes. A bullet you type without an id or a
comment is an entry from "you" and gets both at the next write; moving a
bullet to another section changes its type; deleting it removes the entry.
Text outside our five sections is kept as it is.

A journal page starts with YAML frontmatter whose values are JSON
(`title`, `task_id`, `status`, `date`, `branch`, `agents`, `started`, `ended`,
`commits_since_start`, `files_changed`, `tags`), then `## Goal`,
`## Outcome`, `## Plan`, `## Decisions`, `## Dead ends`, `## Checks`,
`## Failing at the end`, `## Files`, `## Handoffs`, `## Session summary` and
`## Memory` (links to the entries this task added). Add `locked: true` to the
frontmatter and agent-autosave never rewrites that page. The file name is
`YYYY-MM-DD-<goal slug>-<HHMMSS>.md`, from the task's start in your local
time zone. Every date people read (the file name, `date`, memory `added`,
`journal` and `memory list`, recall lines) is the local date; `started` and
`ended` stay UTC timestamps. A page keeps the name it was first written
with, so pages from earlier versions keep their UTC-dated names.

## A real folder

The folder at the end of the getting-started tour, after one task was archived (locks hidden):

<!-- run: tree -->
```console
$ find .autosave | sort
.autosave
.autosave/MEMORY.md
.autosave/SAVE.md
.autosave/config.json
.autosave/events.jsonl
.autosave/history
.autosave/history/t_20261015_015800000000.json
.autosave/install.json
.autosave/journal
.autosave/journal/2026
.autosave/journal/2026/10
.autosave/journal/2026/10/2026-10-15-use-your-todo-task-tool-to-make-a-3-step-plan-015800.md
.autosave/memory
.autosave/memory/.lock
.autosave/memory/candidates.jsonl
.autosave/memory/index.json
.autosave/memory/state.json
.autosave/save.json
.autosave/task.json
```
<!-- end run -->

## See also

- [Contracts](contracts.md): the stable command output built on these files
- [Compiler](compiler.md): how `save.json` is derived from `events.jsonl`
- [Configuration](configuration.md): every `config.json` field
- [Save format, version 1](spec/save-format-v1.md): the frozen formats and the version policy
