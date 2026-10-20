# The handoff benchmark

**Who it's for:** Anyone who wants to measure whether a handoff saves work, on their own machine and agents.

`bench/handoff/` asks one question: when agent A is interrupted mid-task,
does agent B finish it, and how much does it redo?

## The tasks

`bench/handoff/tasks.py` builds 10 small Python projects in throwaway git
repos: 4 bug fixes with failing tests, 5 multi-step features with a plan,
and a refactor. Each plan step has its own test, which fails until the step
is done, so the runner can tell which steps were finished without asking
the agent. The acceptance test is the whole suite. Each task has an
interruption point K: the number of recorded tool events after which A is
stopped.

## What a run does

For each task:

1. agent-autosave is set up for both agents in a fresh repo and A gets the
   goal and the plan.
2. After K tool events in the ledger, A's whole process group is killed.
3. The repo is copied into two arms, and B is started in each with only
   "Continue the task in this repo.":
   - **autosave**: the copy as A left it, so B's session start is handed the task.
   - **control**: agent-autosave is removed and its data deleted (the save,
     MEMORY.md, the journal), then B's hooks are put back with no instruction
     block, no MCP server, no status line and nothing given to a session: no
     start block, no project memory, no prompt recall, no handoff ask, and
     agentic-stack's `.agent/` left alone. They only record what B does, so
     both arms are measured the same way.

## What it measures

| Metric | How |
|---|---|
| Finished | The task's whole test suite passes after B. |
| Redone edits | B's edits to the files of steps A had already finished. |
| Repeated commands | B's passing commands that A had already run and passed. |
| Wall time | How long B ran. |
| Tokens | From B's transcript: Claude Code's `message.usage`, Codex's `total_token_usage`. `n/a` when it reports none. |

Results go to `bench/results/<date>.json` and `.md`.

## Run it

It needs macOS or Linux, git, pytest and the agents, signed in. Each agent
run may take up to 30 minutes (`--timeout`).

```sh
python bench/handoff/run.py --a claude-code --b codex
python bench/handoff/run.py --a codex --b claude-code --tasks add-word-count,split-utils
```

Agents run without prompts for permission (`claude -p
--dangerously-skip-permissions`, `codex exec --sandbox workspace-write`), in
the throwaway repos only. `--fake` runs a scripted agent instead, offline:
it tests the runner and measures nothing, and its results are named
`<date>-fake.*`. No benchmark numbers are published until a real run.

## See also

- [How a save works](how-a-save-works.md)
- [Switching agents](switching.md)
- [Testing](testing/README.md)
