# Resume, status, notes and history (macOS, about 5 minutes)

**Who it's for:** Whoever checks `resume` between real agents before a release.

Runs in a throwaway repo under /tmp with the Claude Code and Codex logins you
already have; nothing here logs into anything new. Everything except the two
real launches in step 4 is covered by `tests/test_commands.py` and
`tests/test_resume_rules.py`.

## 1. Install from your checkout

Follow [Install from a checkout](README.md#install-from-a-checkout).

## 2. Make a scratch repo with an unfinished task

```sh
rm -rf /tmp/aa-resume && mkdir /tmp/aa-resume && cd /tmp/aa-resume && git init -q && git commit -q --allow-empty -m start
codex          # FIRST: trust the folder (Codex asks), then /quit; init skips Codex in untrusted folders
agent-autosave init --agents claude-code,codex
codex          # trust the 6 agent-autosave hooks in /hooks
```

Send `Create add.py with a function add(a, b) that returns a - b, and a test test_add.py that checks add(2, 2) == 4. Run python3 -m pytest -q test_add.py once and stop; do not fix it.`
then `/quit` and **wait 3 seconds** (Codex returns the terminal before its
SessionEnd hook finishes; `status`/`show`/`resume` wait up to 1 s themselves).

## 3. Status, notes, staleness, show

```sh
agent-autosave note "Keep add() signature unchanged"
agent-autosave note "Do not touch CI" --type warning
agent-autosave status          # Status: paused, the failing pytest, "Handoffs: none"
git commit -q --allow-empty -m one && git commit -q --allow-empty -m two
agent-autosave status          # first line: "! This save may be stale: 2 commits landed since."
agent-autosave show --context  # the block an agent gets
```

## 4. Resume into each agent (the manual part)

```sh
agent-autosave show --context  # first line: [agent-autosave resume context]; Warnings: ...
agent-autosave resume claude-code --dry-run    # prints: <path>/claude '<the block>'
agent-autosave resume claude-code
```

Claude Code opens with the block as its first prompt. Check it names Codex,
the failing `python3 -m pytest -q test_add.py`, the decision and the warning.
Let it fix add.py (or not), then `/exit`.

```sh
agent-autosave status          # same task_id as before resume; "Handoffs: handed off 1 time"
agent-autosave history         # still empty — resume must NOT have archived the task
agent-autosave resume codex --dry-run
```

If the task is unfinished, `agent-autosave resume codex` opens Codex with the
block; `/quit`, **wait 3 seconds**. The task_id must still be the add.py one
(`agent-autosave status --json` → `data.task_id`). If the save is already
`done`, resume exits 1 (`nothing_to_resume`).

## 5. Clear and history

```sh
agent-autosave clear           # Archived t_... to .autosave/history/; new task t_...
sleep 3                        # any late Codex SessionEnd must not dirty the new task
agent-autosave history         # archived status is paused/interrupted/done — never active
agent-autosave resume codex --dry-run; echo "exit $?"   # exit 1: empty task, nothing to resume
agent-autosave status --json   # goal "", no agents
# Git line / files must not mention __pycache__, *.pyc, .DS_Store, node_modules:
grep -E '__pycache__|\.pyc|\.DS_Store|node_modules' .autosave/SAVE.md && echo FAIL || echo "no junk"
cat .autosave/errors.log 2>/dev/null || echo "no errors.log"
```

Expected: the same task_id before and after each resume, no `active` status
in history, a non-zero exit after clear, and the warning in `show --context`.
Clean up with `rm -rf /tmp/aa-resume`.

## See also

- [How a save works](../how-a-save-works.md)
- [Testing overview](README.md)
- [Claude Code tests](claude-code.md) and [Codex tests](codex.md)
