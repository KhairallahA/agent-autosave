# Examples

Each folder holds the `.autosave/` that agent-autosave wrote for one task
that moved from one agent to another:

| Folder | What happened |
| --- | --- |
| [claude-to-codex](claude-to-codex/README.md) | Claude Code plans, writes `hello.py`, runs a failing command and quits; Codex starts and gets the save |
| [codex-to-claude](codex-to-claude/README.md) | The same task the other way round, with the plan recorded through `agent-autosave plan` |
| [interrupted-by-limit](interrupted-by-limit/README.md) | Claude Code is cut off by a rate limit mid-task; Codex starts and is told why the task stopped |
| [low-battery-handoff](low-battery-handoff/README.md) | Codex nears its 5-hour limit, is asked for a handoff note and writes one; Claude Code starts with it |
| [one-key-switch](one-key-switch/README.md) | Under `agent-autosave go`, Claude Code writes a handoff note at 88% and hits its limit; `go` asks once and starts Codex with the save |
| [memory-recall](memory-recall/README.md) | Codex records a decision; once accepted into `MEMORY.md`, a later Claude Code session is given it at start and on a related prompt |

Each folder has:

- `.autosave/events.jsonl` (the ledger), `save.json` and `SAVE.md`: the
  save at the end of the second agent's session.
- `before/SAVE.md`: the save when the first agent stopped.
- `context.txt`: the block the second agent's SessionStart hook gave it.
- `ask.txt` (low-battery-handoff, one-key-switch): the handoff ask the
  first agent's prompt was given; `go.txt` (one-key-switch): what `go`
  printed and the first prompt it started Codex with.
- `replay.py`: rebuilds all of the above from `events.jsonl` with the
  compiler the hooks use. `python replay.py --check` exits 1 if a committed
  file differs.
- `README.md`: the story, then those views.

`tests/test_examples.py` runs every `replay.py --check` and checks each
README against the replay; `tests/test_schemas.py` checks `save.json`
against the schemas in `schemas/`.

## Where they come from

They are not written by hand. `scripts/build-examples.py` takes two real
recordings from `tests/fixtures/` (one session per agent), feeds their hook
payloads through the real `hook` code into a scratch project, and keeps the
ledger:

- The two sessions were recorded on separate runs, so the second agent's
  session is moved to start three minutes after the first one quit. Every
  other timestamp is the recorded one.
- Paths become `~/projects/hello`, and the agents' transcript paths become
  neutral paths in each agent's usual layout. The recordings were already
  scrubbed of secrets and home directories, and the ledger was redacted as
  it was written.
- Where the agent ran `agent-autosave plan`, the script runs it too, as the
  agent did.
- In the recording, the second session's reply described that agent's own
  earlier run, so it is left out. The session's hooks are kept.
- Codex exit codes are read from a rollout file built from a real Codex
  rollout row. The script sets each command's code to the one the session
  reported (7 for the failing one, 0 otherwise).
- These are replays, not live projects, so they hold no git facts and
  `SAVE.md` says "Git state unavailable".

- interrupted-by-limit is the exception to "real recordings only": no real
  rate-limit `StopFailure` has been recorded yet, so its one `StopFailure`
  is the real payload with `error` changed to `"rate_limit"` (the documented
  value). Its README says so.

- low-battery-handoff and one-key-switch add a usage reading (Codex's
  rollout row, Claude Code's status line payload) and the handoff note the
  agent was asked for; one-key-switch also has interrupted-by-limit's rate
  limit.

- memory-recall replays Codex's session from codex-to-claude with two
  added events, a decision note and the user's `done`; its `replay.py`
  writes the journal page, `MEMORY.md` and what recall gives Claude Code.

To rebuild them, run `python scripts/build-examples.py` and review the diff.
