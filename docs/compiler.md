# Compiler

**Who it's for:** Contributors changing how a save is built from the ledger, and anyone checking why a field has the value it has.

`compile_events(events, git_state=None, config=None, *, started=None,
task_id=None, redactor=None)` returns a version-one save. `serialize(save)` emits
canonical UTF-8 JSON with sorted keys, indentation, and one trailing LF.
`render(save, config=None)` emits the Markdown view. Inputs are not mutated.

The fold uses ledger order. The first substantive prompt becomes the goal;
subsequent substantive prompts become steering. Plans replace, actions accumulate,
and a successful rerun resolves only the same trimmed command. An empty or
missing plan never proves completion. Explicit interruption persists through
session notifications and turn end; a prompt clears it. Compaction is provisional
until a subsequent turn end. Sessions and every agent switch are retained in
order. The field-by-field contract is [below](#compiler-rules); the
[save format](spec/save-format-v1.md) defines the files it produces.

TaskStore handles init, clear, and the first prompt after completion outside the
pure fold. It archives queued events as well as the previous snapshot.

## Compiler rules

The compiler is a pure function: the same events, git state and config
always produce the same save, byte for byte. The golden tests check these
rules.

### Task boundaries

- A task starts at `init`, after `agent-autosave clear`, on the first
  `prompt` after a save reached `done`, or on a `SessionStart` with source
  `clear` once the work looks finished. Finished means a goal, nothing
  failing, no unknown exit and no unfinished plan step
  ([continuity](design/continuity.md#task-boundaries-without-a-plan)).
- Starting a task archives the previous save to `history/` and gives the
  new one a `task_id` (`t_<UTC date>_<time>`).
- Only events since the task started are compiled. Older events stay in the
  ledger for history.

### Field rules

| Field | Rule |
| --- | --- |
| `goal` | The first substantive prompt of the task. Prompts under 20 characters are skipped, and so are prompts that match a continuation list ("continue", "go on", "yes", "ok", "keep going"). |
| `steering` | Later substantive prompts: the newest 5, each capped at 300 characters. A prompt identical to the one before it is not added again. `latest_request` is the newest one. |
| `plan` | Three sources: (a) the agent's to-do tool (`origin` `tool`); (b) Markdown checklist lines in the agent's latest message (`origin` `message`, at most 20), used only while no to-do tool has run in the session; (c) `agent-autosave plan set` and `plan done <n>`. Events fold in ledger order. A `plan_set`, or a plan from the to-do tool, replaces the whole list. `plan_done` marks one step of the current plan done, and is ignored if out of range. A `message` plan applies only while the task has no other plan. With no plan events, the section is omitted. |
| `last_actions` | The newest 10 `tool` events, one line each, newest first. agent-autosave's own commands are bookkeeping. In a chain built with `;`, `&&`, `\|\|` or newlines, they are dropped from the start or end of the chain. If one sits in the middle, the whole command is kept. A command made only of them is skipped. Read-only tools are listed as "Looked at" instead. |
| `failing` | The newest shell command with a non-zero exit that has not run with exit 0 since. A run with `exit: null` (unknown) neither resolves an earlier failure nor creates one. Commands are compared after trimming whitespace. |
| `unknown_exit` | Commands whose newest run had `exit: null` and that are not failing: newest first, at most 5. They are shown as "exit unknown" (or "exit hidden by a pipe"), never as a pass. |
| `files_touched` | Files from edit and write tools, plus git's dirty list: de-duplicated, at most 30. |
| `git` | Read at rebuild time, with a 150 ms timeout per call. `null` outside a git repo or on a timeout. |
| `decisions` | `note` events of type `decision`, oldest first. |
| `last_report` | The agent's newest non-empty last message, at most 2,000 characters. |
| `next_action` | The first match wins: "Nothing: the task is finished." for a done task; then "Make `<failing>` pass."; then the step marked doing; then the first todo step; then "Re-run `<newest unknown_exit command>` …"; then, when actions and a last message were recorded, "Read the agent's last message, check git, and ask the user what is left."; then the last steering prompt; then the goal. |
| `agents` | One entry per agent session that wrote events in this task, in order. Every switch between agents also appends `{from, to, at, reason}` to `handoffs`. |

### Status states

| Status | Set when | Cleared when |
| --- | --- | --- |
| `active` | A `session_start` or a `prompt` arrives | An `interrupted` event, a `session_end` or a done condition |
| `interrupted` | An `interrupted` event, or a `compact` with no later `turn_end`, `session_start` (source `compact`) or `session_end` | Any agent sends a `prompt` |
| `paused` | A `session_end` with the plan unfinished | The next `prompt` |
| `done` | `agent-autosave done` (a `task_done` event); or every plan step done, nothing failing, no `unknown_exit` command, and a `turn_end` | The next `prompt` starts a new task |

An absent or empty plan never proves completion.

### Size and safety rules

- `SAVE.md` is capped at `compile.save_md_max_chars`: 12,000 characters by
  default, at most 16,000. Over the cap, it trims in this order, stopping as
  soon as the text fits: `last_actions` to 5, `steering` to 3, the failing
  output to 10 lines, `files_touched` to 15. If it still doesn't fit, the
  details are cut, but the next action and the git reminder are kept.
- Every snapshot is written to a temp file in `.autosave/` and renamed into
  place, so a crash never leaves half a save. Ledger writes are locked
  appends. An incomplete final line is repaired before the next append.
- The ledger rotates at 5 MiB. The old file moves to `history/`, and the
  active task's events are carried over.
- Readers reject malformed events and future format versions with an
  error. They never silently drop history.

## Rule-to-test map

Every rule above has direct tests, in addition to
26 checked-in synthetic normalized event streams with byte-exact JSON and Markdown
expectations in `tests/fixtures/compiler/`.

| Rule | Implementation | Tests |
| --- | --- | --- |
| Pure, deterministic, byte-identical, no input mutation | `agent_autosave/compiler.py` | `test_compiler.py::test_determinism_no_input_mutation_and_empty_boundary`, `test_golden_streams` |
| Init / clear boundaries, archive, fresh identifiers | `agent_autosave/state.py` | `test_state.py::test_init_clear_archive_latest_events_and_same_timestamp_boundaries` |
| First prompt after done starts new task | `agent_autosave/state.py` | `test_state.py::test_prompt_after_done_starts_and_archives_task_even_without_rebuild` |
| Exclude old events; preserve ledger history | `agent_autosave/compiler.py`, `state.py`, `ledger.py` | `test_compiler.py::test_explicit_boundary_excludes_old_and_other_task_events`, init/clear test above |
| First substantive goal, threshold and continuations | `agent_autosave/compiler.py` | `test_compiler.py::test_goal_threshold_continuations_and_first_substantive_prompt` |
| Steering newest five, 300 characters | `agent_autosave/compiler.py` | `test_compiler.py::test_steering_keeps_newest_five_capped_at_300` |
| Latest full plan, omit absent plan | `agent_autosave/compiler.py` | `test_compiler.py::test_latest_plan_replaces_whole_list_and_absent_plan_is_omitted` |
| Ten actions, newest first, one line | `agent_autosave/compiler.py` | `test_compiler.py::test_last_actions_newest_ten_one_line_each` |
| Shown actions: project-relative, temp paths `<tmp>`, at most 100 chars at a word boundary | `agent_autosave/display.py` | `test_display.py::test_last_actions_drop_temp_paths_and_the_project_root` |
| Newest unresolved failure, trimmed command equality | `agent_autosave/compiler.py` | `test_compiler.py::test_failure_resolution_trimmed_command_and_older_unresolved_failure` |
| Edit/write plus git files, deduplicate, cap thirty | `agent_autosave/compiler.py` | `test_compiler.py::test_files_from_edits_writes_and_git_deduplicated_capped` |
| Git at rebuild, null outside repo / timeout, 150 ms per call | `agent_autosave/gitstate.py`, `state.py` | `test_state.py::test_rebuild_collects_git_at_compile_time`, `test_gitstate.py` |
| Decisions oldest first | `agent_autosave/compiler.py` | `test_compiler.py::test_decisions_oldest_first_other_notes_remain_only_in_ledger` |
| All next-action priorities | `agent_autosave/compiler.py` | `test_compiler.py::test_next_action_precedence` |
| Ordered agent sessions and every handoff | `agent_autosave/compiler.py` | `test_compiler.py::test_agents_per_session_order_turns_transcripts_and_every_switch` |
| Active and explicit interruption; prompt recovery | `agent_autosave/compiler.py` | `test_compiler.py::test_explicit_interruption_persists_until_prompt` |
| Compaction with / without later turn end | `agent_autosave/compiler.py` | `test_compiler.py::test_compact_without_later_turn_end_is_interrupted` |
| Paused and done conditions | `agent_autosave/compiler.py` | `test_compiler.py::test_paused_until_prompt_and_done_requires_all_three_conditions` |
| 12,000-character cap (at most 16,000) and exact reduction order | `agent_autosave/render.py` | `test_render.py::test_ordered_reductions_stop_as_soon_as_budget_met`, `test_hard_cap_on_individually_oversized_fields_preserves_next_action_and_reminder` |
| Atomic snapshots, failed writes, recovery | `agent_autosave/storage.py`, `state.py` | `test_storage.py::test_atomic_replace_failure_preserves_previous_and_cleans_temporary`, `test_state.py::test_rebuild_repairs_stale_markdown_and_propagates_logged_errors` |
| 5 MiB rotation with active events carried forward | `agent_autosave/ledger.py` | `test_ledger.py::test_default_five_mb_rotation_preserves_active_task`, `test_rotation_discards_old_task_from_active_file_but_archives_it` |
| Reject corruption / future versions; repair partial final line | `agent_autosave/events.py`, `storage.py`, `ledger.py` | `test_events.py`, `test_storage.py`, `test_ledger.py` |
| Ignore tools / paths and configured limits | `agent_autosave/compiler.py` | `test_compiler.py::test_config_limits_and_ignored_capture` |
| `exit: null` is unknown: never a pass or a failure, blocks `done` | `agent_autosave/compiler.py`, `render.py` | the `exit: null` section of `test_compiler.py` |

Run `python -m pytest tests/test_performance.py -s` for p95 timings over 20 runs
following a warmup. The compiler measurement is the pure 1,000-event fold;
filesystem persistence and git subprocess time are separate. Append and Markdown
rendering have their own 50 ms and 20 ms checks. Whole-hook timing has its own
tests in the same file and in `scripts/bench.py`.

To intentionally update goldens, run `python scripts/update-goldens.py`, inspect
the full diff, and run the independent rule tests. These streams are synthetic
normalized events, not agent recordings; recordings live under
`tests/fixtures/<agent>/`.

## What it produces

The compiler's output for the getting-started task, as `SAVE.md`:

<!-- run: show-md -->
```console
$ agent-autosave show --md
<!-- agent-autosave v:1 -->
# agent-autosave — paused task

**Status:** paused, 2026-10-15T02:00:42.831319Z
**Goal:** Use your todo/task tool to make a 3-step plan: write hello.py, run it, add a failing check. Then do step 1 only: write hello.py that prints "hello autosave".
**Latest request:** Run python3 hello.py. Then run python3 -c "raise SystemExit(7)" and stop there; do not fix it.
**User steering:**
- Run python3 hello.py. Then run python3 -c "raise SystemExit(7)" and stop there; do not fix it.

## Plan
- [x] Write \`hello.py\`
- [x] Run it
- [ ] Add a failing check: the failing command above ran, but nothing in the repo records it as a check yet.

## Where it stopped
- Ran python3 -c "raise SystemExit(7)" (exit 7)
- Ran python3 hello.py (exit 0)
- Wrote hello.py

## Failing
`python3 -c "raise SystemExit(7)"` (exit 7)

    Exit code 7

## Files and git
Git state unavailable.
Files: hello.py.

## Agent's last message
    Both commands ran:

    1. `python3 hello.py` printed `hello autosave`.
    2. `python3 -c "raise SystemExit(7)"` failed with **exit code 7**, as intended. I've stopped there and left it as is.

    Progress:
    - [x] 1. Write `hello.py`
    - [x] 2. Run it
    - [ ] 3. Add a failing check: the failing command above ran, but nothing in the repo records it as a check yet.

(claude-code, 2026-10-15T01:59:47.227682Z)

## Decisions
None recorded.

## Next action
Make `python3 -c "raise SystemExit(7)"` pass.

## Before you continue
Run `git status` and confirm the files above match. If they don't, trust git over this save.
```
<!-- end run -->

## See also

- [Local format](format.md): where `save.json` and `SAVE.md` live
- [How a save works](how-a-save-works.md): when the compiler runs
- [Architecture](architecture.md): the modules around it
- `schemas/save.schema.json`: the schema every save is checked against
