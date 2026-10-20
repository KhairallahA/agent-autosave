# Working with agentic-stack

**Who it's for:** Anyone using [agentic-stack](https://github.com/codejunkie99/agentic-stack) and agent-autosave in the same repo.

agentic-stack is what your agent knows; agent-autosave is where your task
is. agentic-stack keeps an agent's memory, skills and lessons in `.agent/`;
agent-autosave records the task in progress so another agent can pick it
up. In one repo they share, without either replacing the other.

What follows was read from agentic-stack's repository at commit
`11a75e16b6cb08a436b3124474273fdb9f08b4de`. No code was copied.

## Turning it on

Nothing to do: `integrations.agentic_stack` is `auto`, which is on when the
repo has `.agent/AGENTS.md` and `.agent/memory/`. The wizard asks when it
finds them; `off` stops all of the below. `doctor` shows an
`agentic-stack` line when the folder is here.

## What agent-autosave does

- **WORKSPACE.md.** `.agent/memory/working/WORKSPACE.md` is agentic-stack's
  live task state. agent-autosave keeps one marked block in it, between
  `<!-- agent-autosave:start -->` and `<!-- agent-autosave:end -->`, with the
  same headings (Current task, Open files, Active hypotheses, Checkpoints,
  Next step) filled from the save, every value quoted as data. Open files
  lists the files the agent edited, then the ones it looked at, then other
  changed files. While the integration is on, agentic-stack's own `.agent/`
  folder is left out of the files touched and of the git file lists (a fresh
  install is untracked). The rest of
  the file is untouched. agentic-stack archives a WORKSPACE.md that is two
  days old when Claude Code stops; the block is written again at the next
  turn.
- **Cases.** When a task is finished, agent-autosave runs
  `python .agent/tools/track.py add case --intent <goal> --outcome <outcome> --session <task id>`
  with a 2-second limit, so it lands in agentic-stack's
  `memory/cases/agent_case-<date>.md`. Tasks finished before the
  integration was on are not sent.
- **Lessons.** Accepted lessons in `.agent/memory/semantic/lessons.jsonl`
  (the newest row per id, minus ones an accepted lesson supersedes) are
  recalled on prompts like project memory, labeled "agentic-stack lesson".
  In Claude Code they are skipped when `.claude/settings.json` runs
  agentic-stack's `claude_code_session.py` hook, which recalls them itself.

agent-autosave writes nothing else in `.agent/`. `remove` (of the last
agent) takes out only its block in WORKSPACE.md.

## Installing both

Install agentic-stack first, the way its README says (for Claude Code:
`agentic-stack claude-code`, or `./install.sh claude-code <repo>` from a
clone), then run `agent-autosave init`. agent-autosave merges its hooks into
`.claude/settings.json` and adds a marked block to `CLAUDE.md`, keeping
agentic-stack's hooks and text.

The other way round loses agent-autosave's Claude Code wiring:
agentic-stack's Claude Code adapter replaces `CLAUDE.md` and
`.claude/settings.json` when they exist (`merge_policy: overwrite` in its
`adapters/claude-code/adapter.json`). `agent-autosave doctor` then shows the
missing hooks in red, and `agent-autosave add claude-code` puts them and the
block back without touching agentic-stack's entries. `remove` later takes
out only agent-autosave's hooks.

## See also

- [Memory](../memory.md): project memory and recall
- [Configuration](../configuration.md): `integrations.agentic_stack`
- [Security](../security.md): what agent-autosave runs and writes in `.agent/`
