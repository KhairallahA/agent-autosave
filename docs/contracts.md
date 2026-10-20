# Contracts

**Who it's for:** Anyone scripting against agent-autosave or writing an agent integration that reads its JSON.

What scripts and agents can rely on. A change to anything on this page is a
breaking change and is called out in the changelog.

## Output

Every command except `hook` prints one JSON object when `--json` is given or
stdout is not a terminal. In a terminal, success prints human text on stdout
and failure prints `error:` and `hint:` lines on stderr. `--quiet` silences
human success output, even when piped, unless `--json` is also given; errors
are always printed. `--json` and `--quiet` work before or after the command
name.

Success:

```json
{"ok": true, "data": {"...": "..."}, "meta": {"version": "0.1.0", "save_v": 1, "elapsed_ms": 12}}
```

Failure:

```json
{"ok": false, "error": {"type": "not_found", "code": "not_initialized", "message": "No .autosave/ directory exists here or in a parent directory.", "hint": "Run agent-autosave init", "detail": {}}, "meta": {"version": "0.1.0", "save_v": 1, "elapsed_ms": 3}}
```

- `meta.version` is the package version, `meta.save_v` the save format
  version (see [format.md](format.md)), `meta.elapsed_ms` the command's own
  time.
- `error.type` is the category to branch on; it decides the exit code.
  `error.code` is the specific, stable identifier within it. `message` and
  `hint` (the next thing to run or do) are for people and may change
  wording. `detail` is an object of extra structured data; it is `{}` when
  there is none.
- Messages never echo raw arguments: argparse errors are replaced by one
  fixed message, because an argument may hold a secret.
- `init` reports non-fatal findings in `data.warnings`, each with its own
  `code`, `message` and `hint`. `doctor` checks carry a `hint` too.

## Errors and exit codes

| Exit | `error.type` | `error.code` | When |
| --- | --- | --- | --- |
| 0 | | | Success |
| 1 | `not_found` | `not_initialized` | No `.autosave/` here or in a parent folder, or no `install.json` for `add` and `upgrade` |
| 1 | `not_found` | `no_save` | `.autosave/` exists but holds no task yet |
| 1 | `not_found` | `unknown_task` | `show --task` or `resume --task` with an id that is in no branch slot and no archive |
| 1 | `not_found` | `nothing_to_resume` | `resume` with no unfinished save |
| 1 | `not_found` | `agent_not_installed` | The agent is not on this machine (`init`, `add`, `resume`) |
| 1 | `not_found` | `not_installed` | `remove` of an agent that is not installed here |
| 1 | `not_found` | `unknown_entry` | `memory accept`, `reject`, `pin`, `forget` or `--replaces` with an id that is not in `MEMORY.md` or waiting for review (or was already decided) |
| 1 | `not_found` | `no_vault` | `vault move` with no `vault.path`, or a vault folder that cannot be used |
| 1 | `not_found` | `no_handoff` | `handoff import` with no `.autosave-handoff.md`, or one with no events |
| 1 | `conflict` | `edited_config` | `remove` (or `share`) found one of our additions edited by hand |
| 1 | `conflict` | `secrets_found` | `share on` found lines in `MEMORY.md` or the journal that look like secrets |
| 1 | `conflict` | `user_level_needed` | The repo's Codex hooks are user-level; pass `--user-level` |
| 1 | `permission` | `codex_untrusted` | Codex does not trust this folder, so it would not run project hooks |
| 1 | | | `doctor` found a red check (the output is still a success envelope) |
| 2 | `invalid_args` | `invalid_args` | Bad flag, argument or combination |
| 2 | `invalid_args` | `not_supported` | A valid request this agent or command cannot do (`resume generic`, `--user-level` without Codex) |
| 2 | `invalid_args` | `invalid_config` | `.autosave/config.json` is not valid configuration |
| 5 | `io` | `adapter_problem` | An agent file could not be read or merged (`init`, `add`, `remove`, `upgrade`) |
| 5 | `internal` | `command_failed` | An unexpected error; details in `.autosave/errors.log` |

New codes may be added within a type. Adding a type, or moving a code to
another type or exit, is a breaking change. Hooks are not on this table:
`hook` always exits 0.

## Hook output

`agent-autosave hook <agent> <event>` is called by agents, not people. It
always exits 0, reads at most 2 MiB of stdin, and prints only what the
agent's hook protocol expects:

| Agent and event | stdout |
| --- | --- |
| Claude Code and Codex `SessionStart` | `{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "<block>"}}` when the save should be injected, else nothing |
| Cursor `beforeSubmitPrompt` | `{"continue":true}`, plus `"additional_context": "<block>"` on a conversation's first prompt, and the recall block when earlier notes match the prompt |
| Claude Code and Codex `UserPromptSubmit` | `{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "<text>"}}` when there is something to add, else nothing. The text is the handoff ask (once per crossing of `handoff.warn_at_percent`), the `[agent-autosave memory]` recall block (see [memory](memory.md)), or both |
| Any other event | nothing |

Any error goes to `.autosave/errors.log`; the agent never sees it.

## Files

`save.json` and `SAVE.md` carry `v: 1`. Their fields and headings are
described in [format.md](format.md) and [compiler.md](compiler.md); new
fields may be added within a version, and a reader must ignore fields it
does not know.

## Performance budgets

Each budget is measured at the 95th percentile, by `tests/test_performance.py` and by
`scripts/check.sh`. "Any hook" is enforced in pytest on the hook's in-process work.
`check.sh` and `scripts/bench.py` report and enforce the whole-process time, including
Python start-up. On a slow machine, set `AGENT_AUTOSAVE_SKIP_STARTUP_BUDGET=1` to skip
that part.

| Operation | Budget | How it's met |
| --- | --- | --- |
| Any hook, start to exit | 300 ms | Standard library only, lazy imports, no git calls outside the compiler |
| Capture and append one event | 50 ms | One locked append, without reading the ledger |
| Compile a task of 1,000 events | 200 ms | Reads only the events since the task started; each git call is capped at 150 ms |
| Render `SAVE.md` | 20 ms | String templates, no Markdown library |
| The status line, whole process | 50 ms | Reads `save.json` only |
| `SAVE.md` size | 12,000 characters by default, at most 16,000 | The trim order in [compiler rules](compiler.md#size-and-safety-rules) |

Claude Code allows a `SessionEnd` hook 1.5 s. agent-autosave settles the save within the
300 ms hook budget.

## Examples

A success envelope, from `status --json`:

<!-- run: status-json -->
```console
$ agent-autosave status --json
{"ok": true, "data": {"status": "paused", "goal": "Use your todo/task tool to make a 3-step plan: write hello.py, run it, add a failing check. Then do step 1 only: write hello.py that prints \"hello autosave\".", "plan_done": 2, "plan_total": 3, "next_action": "Make `python3 -c \"raise SystemExit(7)\"` pass.", "task_id": "t_20261015_015800000000", "updated": "2026-10-15T02:00:42.831319Z", "interrupted": null, "failing": "python3 -c \"raise SystemExit(7)\"", "handoffs": [], "handoff_count": 0, "stale": false, "stale_reason": "", "usage": [], "memory": {"entries": {"decision": 0, "tried": 0, "gotcha": 0, "convention": 0}, "expired": 0, "candidates": 0}}, "meta": {"version": "0.1.0", "save_v": 1, "elapsed_ms": 3}}
```
<!-- end run -->

A usage error in a terminal; the same error piped is an `invalid_args` envelope and exit 2.
One exception: no subcommand at all, with stdin not a terminal and holding a
JSON object (a hook payload), exits 0 and prints nothing.


<!-- run: error-usage -->
```console
$ agent-autosave history --limit 0
error: Invalid command or arguments.
hint: Run agent-autosave history --help to see the arguments.
```
<!-- end run -->

## See also

- [Local format](format.md): the files behind these commands
- [Configuration](configuration.md): the settings `invalid_config` refers to
- [Troubleshooting](troubleshooting.md): when an exit code is not what you expected
