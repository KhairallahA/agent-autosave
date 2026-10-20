# Save format, version 1

**Who it's for:** Anyone writing a tool that reads or writes agent-autosave's files, and contributors changing them.

This is the frozen definition of what agent-autosave keeps on disk. The JSON
Schemas in [schemas/](../../schemas) are normative for every JSON file and
row; this page says what each file is for and adds the Markdown formats
that a schema cannot describe. [tests/spec/](../../tests/spec) checks real
output, the examples and files from earlier versions against both.

All files are UTF-8 with LF line endings. Every JSON file and row has
`"v": 1` (the handoff file says `agent-autosave-handoff: 1`). Timestamps are
UTC, `YYYY-MM-DDTHH:MM:SS[.ffffff]Z`. Task ids are
`t_<UTC date>_<time with microseconds>`, with `_<n>` added when the name is
taken. A version agent-autosave does not know, or a row that breaks its
schema, is an error, never a guess.

| Format | Where | Schema |
|---|---|---|
| Events | `.autosave/events.jsonl`, `.autosave/history/events-*.jsonl` | [event.schema.json](../../schemas/event.schema.json) |
| Save | `.autosave/save.json`, `slots/*.json`, `history/t_*.json` | [save.schema.json](../../schemas/save.schema.json) |
| Task slots | `.autosave/task.json` | [task.schema.json](../../schemas/task.schema.json) |
| `SAVE.md` | `.autosave/SAVE.md` | this page |
| Memory entries | `MEMORY.md` | [memory-entry.schema.json](../../schemas/memory-entry.schema.json) |
| Memory candidates | `.autosave/memory/candidates.jsonl` | [candidate.schema.json](../../schemas/candidate.schema.json) |
| Journal pages | `journal/YYYY/MM/*.md` | [journal.schema.json](../../schemas/journal.schema.json) |
| Handoff file | `.autosave-handoff.md` | [handoff.schema.json](../../schemas/handoff.schema.json) |

## Events

The ledger, `events.jsonl`, is append only: one event per line, in the
order they happened. It is the source of truth; every other file can be
rebuilt from it. Each event has `v`, `ts`, `agent` (`claude-code`, `codex`,
`cursor`, `gemini`, `antigravity`, `copilot`, `opencode`, `generic`, or `cli`
for the agent-autosave command line), `session`, `turn`, `kind`, and
`task_id` once stored. `id` is the agent's own id for the event (a tool use
id, a prompt id); a second event with the same agent, session, kind and id
is a repeat and is skipped.

| Kind | Required | Optional |
|---|---|---|
| `session_start` | `source` | `model`, `transcript` |
| `prompt` | `text` | none |
| `tool` | `tool`, `summary`, `files` | `exit`, `output_tail`, `readonly` |
| `plan` | `items` | `origin` |
| `plan_set` | `steps` | none |
| `plan_done` | `index` | none |
| `compact` | `trigger` | none |
| `interrupted` | `reason` | none |
| `note` | `text`, `type` | `by` |
| `session_end` | `reason` | none |
| `turn_end` | none | `last_message` |
| `task_done` | none | `note` |
| `usage` | none | `limit_pct`, `limit_window_minutes`, `limit_resets_at`, `weekly_pct`, `context_pct` |
| `summary` | `text` | `at`, `trigger`, `pre_tokens`, `post_tokens` |

A `tool` event with `exit` is a command; without `exit` and with `files`
it is an edit, unless `readonly` is true. No file contents, patches or
successful output are ever stored. Text is redacted before it is written.

## save.json

The save is a pure function of the task's events, the task's start, the
git state and the configuration: compiling the same inputs gives the same
bytes (sorted keys, two-space indent, trailing LF). `save.json` is the
current branch's task; `slots/<branch>-<hash>.json` are other branches'
parked saves; `history/t_*.json` are archived tasks. The schema lists every
field.

## task.json slots

`{"v": 1, "current": <branch>, "slots": {<branch>: <task>}}`. Each task has
its `task_id`, `started`, the `start_head` commit and a `cursor`: the
ledger file's `[device, inode]` identity and the byte offset where this
task's events begin. After a clear, `previous` names the archived task and
its sessions that were still open, so their late events still reach it.
`current` is `""` outside git. A `task.json` from before slots (one task
object) is read as the slot of the branch checked out and rewritten in
this shape at the next event.

## SAVE.md

The save as Markdown. Its first line is the version marker
`<!-- agent-autosave v:1 -->`, then `# agent-autosave — <status> task`, the
`**Status:**`, `**Goal:**`, optional `**Latest request:**` and
`**User steering:**` lines, then these sections in this order (optional
ones only when they have content):

1. `## Plan` (once a plan exists)
2. `## Where it stopped`
3. `## Failing`
4. `## Checks` (optional)
5. `## Files and git`
6. `## Agent's last message` (optional)
7. `## Session summary (...)` (optional; the heading names the agent and the token counts)
8. `## Decisions`
9. `## Notes` (optional)
10. `## Next action`
11. `## Before you continue`

It is at most `compile.save_md_max_chars` characters (12,000 by default).
When it would be longer, lists are shortened in a fixed order, and as a last
resort the body is cut and `[Details truncated to fit the save budget.]`
added before `## Next action`. Tools should read `save.json`; `SAVE.md` is
for people and agents.

## Context block markers

Every block handed to an agent starts with one of these lines, so a prompt
that carries one is known to be ours and is never taken for a task:

| First line | Block |
|---|---|
| `[agent-autosave resume context]` | The handoff of an unfinished task (session start, `resume`, `show --context`) |
| `[agent-autosave project memory]` | Accepted memory at the start of a session with no unfinished task |
| `[agent-autosave memory]` | Recalled entries added to a prompt |

Recorded values in a block are quoted and the block says they are data,
not instructions. The resume block ends with
`The full save is in .autosave/SAVE.md. Git is the source of truth if the two differ.`

In files agent-autosave shares with you, its part sits between
`<!-- agent-autosave:start -->` and `<!-- agent-autosave:end -->` (Markdown) or
`# agent-autosave:start` and `# agent-autosave:end` (`.gitignore`,
`.gitattributes`, TOML).

## MEMORY.md

Project memory, edited by people as well as by agent-autosave. It starts
`# Project memory` and has five sections: `## Decisions`, `## Dead ends`,
`## Gotchas`, `## Conventions` and `## Expired`. Each entry is one bullet:

```text
- Stream rows with a generator, not pandas <!-- m:{"type":"decision","source":{"agent":"claude-code","task_id":"t_20261015_015800000000"},"added":"2026-10-15","until":null,"replaces":null} --> ^m-3fa2c1
```

The comment's JSON follows the entry schema; `-->` inside it is written as
`--\u003e`. The `^m-` block id comes last, where Obsidian looks for it.
The section decides the type when the two disagree. A bullet without a
comment or id is an entry from "you" and gets both at the next write. Text
outside the five sections is kept byte for byte, and parsing then writing
gives the same bytes. Shared in git, the file merges with `merge=union`.

## Memory candidates

`memory/candidates.jsonl`: one row per proposed note, with `status`
`pending`, then `accepted` (with the `entry` it became) or `rejected`
(optionally with a `reason`). Rows are never deleted by agent-autosave.

## Journal pages

One page per finished task, `journal/YYYY/MM/YYYY-MM-DD-<goal slug>-<HHMMSS>.md`
(the task's local start). It opens with frontmatter between `---` lines:
YAML whose values are JSON, following the journal schema. A person may add
`locked: true`; a locked page is never rewritten. The sections are
`## Goal`, `## Outcome`, `## Plan`, `## Decisions`, `## Dead ends`,
`## Checks`, `## Failing at the end`, `## Files`, `## Handoffs`,
`## Session summary` and `## Memory`.

## The handoff file

`.autosave-handoff.md`, written by `handoff export`: frontmatter
(`agent-autosave-handoff`, `task_id`, `branch`, `status`, `goal`, `exported`;
YAML whose values are JSON), the task's `SAVE.md`, then the line
`<!-- agent-autosave:events -->` and a fenced `jsonl` block with the task's
events. Importing replays those events, so the save compiles to the same
result on the other machine. Events from rotated ledger segments are not
included.

## Version policy

- Adding is version 1: a new event kind, a new optional field, a new save
  field, a new `SAVE.md` section or a new file. The schemas and this page
  gain the addition in the same change, and every file written before it
  still loads.
- Anything else is version 2: removing or renaming a field, changing a
  field's type or meaning, making an optional field required, or
  reordering `SAVE.md` sections. A version 2 ships with a migration that
  reads version 1 files, and keeps reading them.
- Reading is strict in both cases. agent-autosave checks every event
  against the fields it knows and refuses an unknown kind or field rather
  than guess, so files written by a newer release may need that release.
  Files from every earlier release keep loading; `tests/spec/` and
  `tests/test_compat.py` hold real ones.

## See also

- [Local format](../format.md): every file in `.autosave/`
- [Compiler](../compiler.md): how the save is derived from the events
- [Contracts](../contracts.md): the command output built on these files
