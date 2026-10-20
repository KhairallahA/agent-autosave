# Design: continuity

**Who it's for:** contributors reviewing or extending continuity: crash takeover, task boundaries, the last message, the context block, read-only tools, duplicate events, checks, stuck detection, the low-battery save, the compaction summary and branch slots. For each one, this page says which fields are added, where they are computed, and which tests prove it.

The save format stays `"v": 1`. Every addition is optional in the event
schema and additive in the save schema, so every ledger, `save.json`,
`task.json` and `config.json` written by an earlier version keeps loading
(`tests/test_compat.py` loads fixtures written by an earlier version).

## Shared pieces

| Piece | Where | Why |
| --- | --- | --- |
| `chain()`, `is_readonly(command)`, `check_of(command)` | `agent_autosave/commandline.py` | One shell splitter for stripping our own commands, detecting read-only commands and detecting checks. Pure, no I/O. |
| Event field `id` (string, at most 200 chars, any kind) | `events.py` | The vendor's own identifier, used to drop duplicate events. |
| Event kinds `task_done` {`note`?}, `usage` {...}, `summary` {`text`, `at`?, `trigger`?, `pre_tokens`?, `post_tokens`?} | `events.py` | Task boundaries, the low-battery save and the compaction summary. |
| Note types `handoff` and `tried` | `events.py`, `note` command | The low-battery save asks for a handoff note. The context's "tried" line (priority 8) needs a type to read from. |

## Crash takeover

- `loader.handoff_from_active(save, agent, session)` also returns True when
  `session` is given, differs from `save.last_session.session`, and that last
  session did not end. This applies to the same agent too. The existing wording
  "<Agent> was last active at HH:MM and may still be running; the task is
  unfinished." is kept.
- A start whose session id equals `last_session.session` (a resume) is not a
  handoff.
- Tests: `tests/test_continuity_crash.py::test_killed_session_is_injected_into_next_session`,
  `::test_resuming_the_same_session_injects_nothing`,
  `::test_two_live_sessions_both_see_may_still_be_running`.

## Task boundaries without a plan

- `agent-autosave done [--note TEXT]` appends `task_done` (agent `cli`), then
  rebuilds. The compiler sets `status = "done"`. The existing prompt boundary in
  `TaskStore.append` (a non-resume prompt while the save is done starts a new
  task) does the rest. With no `task.json` it exits 1 with `nothing_to_finish`.
  The note, if any, is stored as `notes[] {type: "decision"}`, the same as `save --note`.
- `SessionStart` with source `clear` starts a new task when
  `TaskStore.clear_ends_task(save)` holds: a goal, no `failing`, no
  `unknown_exit`, and no plan step that is not done. The check is in
  `TaskStore.append` (before the start event is tagged). `capture.handle` uses
  the same predicate so a clear that starts a task injects nothing.
- `save.latest_request` (string, at most 300 chars) is the newest steering
  prompt, or `""`. It is shown in SAVE.md as `**Latest request:**` under the
  goal, and in the block as `Latest request: "..."` right after `Goal:`.
- `done` is the user's command only. No instruction block mentions it: an
  earlier block told agents to run it, and on a real run they ran it after
  every reply, so every prompt became its own finished task and nothing was
  handed off. `upgrade` compares installed block text with the current
  resources, so installs that still have the line are flagged ("text
  changed") and `upgrade --yes` removes it
  (`test_upgrade_flags_and_removes_a_block_with_the_done_line`,
  `test_no_block_mentions_done`).
- A done task's `next_action` (and so the suggested step) is "Nothing: the
  task is finished." (`test_display.py::test_a_done_task_has_nothing_next`).
- With no plan, no failing command and no unknown exit, a task with recorded
  actions and an agent's last message has the next step "Read the agent's
  last message, check git, and ask the user what is left." The goal stays
  the next step only when no actions were recorded
  (`test_next_step_and_report.py`).
- Tests: `tests/test_continuity_tasks.py` (OAuth then CSV with `done`, with a
  clear, a clear while failing, neither).

## The agent's last message

- `events.prepare` caps `last_message` at 2000 chars.
- The compiler keeps `last_report` (string or null: the newest non-empty
  `turn_end.last_message`, redacted, spaces collapsed within each line, line
  breaks kept with at most one blank line in a row, at most 600 chars;
  `display.report`),
  `last_report_agent` and `last_report_at`. Cut with `display.shorten`: at
  the last word boundary, ending in "…" (the context line likewise at 400).
- SAVE.md: `## Agent's last message` as an indented code block (four
  spaces per line, control characters removed): its lines stay literal, so
  the message keeps its line breaks and cannot open a heading, fence, list
  or HTML; the agent and time follow on their own line. Block:
  `Agent's last message: "..."` on one line with Markdown markers removed
  (code fences, `**`, a leading list dash or heading hashes;
  `display.plain`), then JSON-quoted, at most 400 chars, priority 7.
- Tests: `tests/test_continuity_report.py`, `tests/test_next_step_and_report.py`.

## The context block

- Config: `inject.max_chars` default 4000; new `inject.resume_max_chars`
  default 8000 (used with `force=True`: `resume`, `show --context`); both
  between 500 and 12000. `compile.save_md_max_chars` default 12000, at most
  16000 (at least 256 as before).
- `config.json` files written by `init` before this change hold the old
  defaults explicitly (1500 and 8000). `upgrade` reports them and
  `upgrade --yes` rewrites exactly those two values when they still equal the
  old defaults (the old file is kept as `config.json.bak`).
- `loader.sections(save, ...)` returns ordered sections
  `[{name, priority, lines}]`. `loader.pack(sections, budget)` drops whole
  sections from priority 13 up until the block fits. Last resort: the
  suggested step's value is shortened before it is quoted, so no quoted value
  is ever cut. The marker/status header (priority 1), the suggested step and
  the closing line are always kept.
- Priorities: 1 header; 2 warnings and handoff notes; 3 goal and latest
  request; 4 failing (command, exit, last 3 output lines quoted); 5 stuck; 6
  plan (N of M, unfinished steps with state, at most 8); 7 last message; 8
  decisions and tried notes (newest 3 each); 9 project memory (see
  [memory](memory.md)); 10 checks; 11 last actions (newest 5); 12 git
  (branch, diffstat, at most 8 changed files, at most 5 looked-at files); 13
  usage and session summary.
- `show --context --explain` prints each section with its size and `kept` or
  `dropped (budget)`; JSON returns `{"context", "sections": [{name,
  priority, chars, state}], "budget"}`.
- Tests: `tests/test_context_v2.py` goldens at 500, 1500, 4000, 12000 in
  `tests/fixtures/context/`; a property test over many saves and budgets
  (never over budget; marker and suggested step always present).

## Read-only tools

- Claude normalizer: Read, Glob, Grep, LS, NotebookRead, WebFetch,
  WebSearch, TodoRead give tool events with `readonly: true`. Read
  (`file_path`) and NotebookRead (`notebook_path`) carry a project-relative
  path in `files`. MultiEdit (`file_path`) and NotebookEdit (`notebook_path`)
  become edits. Sources: Claude Code's `sdk-tools.d.ts` (npm package,
  2.1.87). MultiEdit, NotebookRead, LS and TodoRead are not in that file;
  they are handled only when their payload carries the field.
- Shell read-only (`commandline.is_readonly`): every chained or piped part
  starts with an allowed reader (cat, head, tail, less, ls, tree, pwd, wc,
  file, stat, rg, grep, fd, find without `-delete`/`-exec`/`-execdir`/`-ok`,
  `sed -n` without `-i`, awk without `-i`, git status/diff/log/show), and the
  command has no `>`/`>>` redirect to a file, no `tee`, no in-place flag.
  Redirects to `/dev/null` (`>`, `2>`, `&>`, `>>`) and fd duplication
  (`2>&1`, `1>&2`) are allowed: they write nothing. Computed in
  the compiler, so old ledgers benefit.
- Compiler: a successful read-only event never enters `last_actions`; its
  files go to `files_read` (newest 10 unique, ignore list applied, shown as
  "Looked at:"). A failing read-only command still counts as a failure.
  Consecutive identical actions collapse to "... x3".
- Tests: `tests/test_commandline.py` (over 40 commands, with traps),
  `tests/test_continuity_readonly.py`.

## Duplicate events

- Ids: Claude `tool_use_id` on tool events and `prompt_id` on prompt events
  (both in `tests/fixtures/claude-code`). Codex `tool_use_id` on tool events
  and `turn_id` on prompt and turn_end events (present in
  `tests/fixtures/codex/sessions/*`). Cursor `tool_use_id` on tool events and
  `generation_id` on prompt events (present in `tests/fixtures/cursor`). None
  are derived from content or time.
- The compiler skips an event whose `(agent, session, kind, id)` it already
  saw in the task.
- doctor: yellow when one agent's hooks are in two layers. Codex
  `.codex/hooks.json` and `~/.codex/hooks.json` (existing). Claude
  `.claude/settings.json`, `.claude/settings.local.json`,
  `~/.claude/settings.json` (local file added). Each comes with the exact fix
  command.
- Tests: `tests/test_continuity_duplicates.py` (the Codex plan-handoff
  recording delivered twice).

## Display of commands and messages

- `display.py` (pure): `shorten(text, limit)` cuts at the last word boundary
  and ends with "…"; `command(text, root)` makes the project root relative,
  shows temp paths (`/tmp`, `/private/tmp`, `/var/folders`, `claude-NNN`
  dirs, `scratchpad`) as `<tmp>` and collapses whitespace.
- The compiler takes the project root as a compile input (`root=`) and adds
  `save.last_actions_shown`: each action's command at most 100 characters,
  the exit and repeat suffix kept. `last_actions` and the ledger keep the full
  text. SAVE.md, the context and `status` use the shown form; a save without
  it is cleaned without the root.
- Tests: `tests/test_display.py`.

## Checks

- `commandline.check_of(command)`: the command when it is a verification
  command (the list in the spec), else None.
- `save.checks`: the newest result per command `{command, exit, at}`, at
  most 6, failing first, then newest first. Unknown exits are skipped.
- SAVE.md `## Checks`; block priority 9.
- Tests: `tests/test_continuity_checks.py`.

## Stuck

- Per command (after `strip_own_commands`), a run of failures with no pass
  between. With 3 or more, `save.stuck = {command, failures, since,
  edits_between}` for the newest such run. `edits_between` counts edit events
  between the first and last failure. The run clears when the command
  passes.
- SAVE.md shows it under Failing; block priority 5.
- Tests: `tests/test_continuity_checks.py::test_stuck_*`.

## Low-battery save

- `transcripts/codex.usage(rows)` reads the newest `token_count` from the
  rows `capture._codex_facts` already reads on Stop (no extra read).
  - `limit_pct`, `limit_window_minutes` and `limit_resets_at` (ISO, from the
    epoch) come from `rate_limits.primary`; `weekly_pct` from `secondary`.
  - `context_pct` is computed the way Codex does from
    `info.last_token_usage.total_tokens` and `info.model_context_window`,
    minus Codex's 12000-token baseline (codex-rs `tui/src/chatwidget.rs`,
    `protocol` `percent_of_context_window_remaining`).
  - Only the snapshot whose `limit_id` is `codex` (or absent, older Codex)
    is read: codex-rs `RateLimitSnapshot.limit_id` names a bucket, and the
    tui's `/status` (`tui/src/status/rate_limits.rs`) lists the `codex`
    bucket first and model-specific quotas after it. A newer reading for
    another bucket would otherwise show its 0%. `used_percent` is a 0-100
    percentage (`protocol.rs` `RateLimitWindow`); missing or null fields
    show nothing, and percentages are rounded, not floored.
  - Deviation: the spec named `total_token_usage`, which is cumulative over
    the session and not context use.
- The normalizer appends `usage` after `turn_end`. `save.usage[agent]` keeps
  the newest, plus `session`, `at` and `crossed_at`: when the current run at
  or above `handoff.warn_at_percent` began. A run ends when usage drops below
  the threshold or `limit_resets_at` changes.
- Config: `handoff.warn_at_percent` (default 85, 50 to 99), `handoff.ask`
  (default true).
- The ask: on `UserPromptSubmit`, after the rebuild, when the agent's usage
  has a `crossed_at`, no `handoff` note was recorded at or after it, and this
  `crossed_at` was not asked before (adapter state), the hook returns
  `hookSpecificOutput.additionalContext` with the ask line. Codex documents
  this output for UserPromptSubmit (developers.openai.com/codex/hooks), so the
  Stop fallback is not needed.
- `status` adds a Usage row; the block shows usage at priority 12.
- Tests: `tests/test_continuity_usage.py` (40 %, 86 %, 92 %, then a reset).

## Compaction summary

- Claude: `transcripts/claude.compact_summary(path)` tail-reads the
  transcript. The path must be under `~/.claude/projects`, end in `.jsonl`,
  and the read is at most 512 KB. It takes the newest `type: system`,
  `subtype: compact_boundary` line, then the `isCompactSummary` line whose
  `parentUuid` is that boundary's `uuid`, else the next `isCompactSummary`
  line in file order; timestamps are never compared (the summary's is
  earlier than the boundary's). `message.content` is a string or a list of
  text blocks. Recorded shape: Claude Code 2.1.302,
  `tests/fixtures/claude-code/transcripts/compact-2.1.302.jsonl`.
- Claude Code can run SessionStart(compact) before it writes those lines (why
  the first build never showed a summary: it read once, too early). Nothing
  found: the adapter state keeps the transcript path as pending, and the
  next Stop or SessionEnd reads it again in the background process (also any
  background rebuild), once per boundary `uuid`.
- Codex: the newest rollout `compacted` item with a non-empty
  `payload.message`, read in the Stop tail; reported once per item timestamp
  (adapter state). An empty message is skipped; history is never rebuilt.
- Event `summary {text, at?, trigger?, pre_tokens?, post_tokens?}`;
  `save.summary = {text (redacted, at most 1200, cut at a word), agent, at
  (the boundary's timestamp), trigger, pre_tokens, post_tokens}`. SAVE.md
  `## Session summary (Claude Code compacted its context: 39.4k to 3.4k
  tokens)`; block priority 12, the context line at most 400 chars.
- Tests: `tests/test_continuity_summary.py`, `tests/test_compaction_recorded.py`.

## Save slots per branch

- `gitstate.head_branch(project)` reads `.git/HEAD` directly. It follows a
  `.git` file's `gitdir:` (worktrees) and returns the branch, or None when
  detached or not a repository. No subprocess.
- `task.json` becomes `{"v": 1, "current": "<branch>", "slots": {"<branch>":
  <task record>}}`. Not in git, the key is `""`. An old single-task file is
  read as the current branch's slot and rewritten atomically in the new shape
  on the first write.
- `TaskStore._task()` switches slots when the branch differs from `current`
  (append, end_session, rebuild). It rebuilds and parks the current save in
  `.autosave/slots/<slug>.json`, switches, then rebuilds that branch's task
  or starts one. A detached HEAD keeps the current slot.
- Ledger rotation keeps the events of every slot's task (and each slot's
  previous task), not only the current one.
- A slot with no goal and no actions is never stale (a new branch's empty
  slot), `test_display.py::test_an_empty_slot_is_never_stale`.
- `status --all` lists slots (branch, status, goal, updated).
  `show --task <id>` and `resume <agent> --task <id>` read a slot or a
  history entry.
- Tests: `tests/test_continuity_slots.py` (main, feature, back; worktree;
  detached; migration).

## See also

- [How a save works](../how-a-save-works.md): the path these fields travel
- [Compiler](../compiler.md): the field rules, including these
- [Local format](../format.md): `task.json` slots and `slots/`
- [Configuration](../configuration.md): the new `inject` and `handoff` fields
