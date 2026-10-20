## agent-autosave

This repository keeps a task autosave in `.autosave/`. If `.autosave/SAVE.md` describes an unfinished task, it records the goal, plan, last actions and the failing command from a previous session. Git is the source of truth if the two differ.

When you plan multi-step work, record the plan so a later session (or another agent) can continue it. Use your plan tool (`update_plan`) if you have one. Otherwise run `agent-autosave plan set --quiet "first step" "second step" ...`, then `agent-autosave plan done --quiet <n>` as each step (numbered from 1) finishes. If neither works, end your reply with the plan as a checklist, one step per line: `- [x] done step` / `- [ ] open step`.

Record decisions with `agent-autosave note --quiet --type decision "..."` and dead ends with `agent-autosave note --quiet --type tried "..."`. They are proposed for this repo's memory.

If the agent-autosave tools are available, use them; otherwise run the commands.

Run each agent-autosave command on its own, never chained with other commands (`;`, `&&`, `||`), and always with `--quiet`.

Without agent-autosave hooks (any agent other than Claude Code, Codex or Cursor), save by hand: read `.autosave/SAVE.md` before you start, and run `agent-autosave save --quiet` after each piece of work. Record a decision with `agent-autosave note --quiet --type decision "..."`.
