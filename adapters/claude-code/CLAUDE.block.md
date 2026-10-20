## agent-autosave

This repository keeps a task autosave in `.autosave/`. If `.autosave/SAVE.md` describes an unfinished task, it records the goal, plan, last actions and the failing command from a previous session. Git is the source of truth if the two differ.

When you plan multi-step work, record the plan so a later session (or another agent) can continue it: `agent-autosave plan set --quiet "first step" "second step" ...`, then `agent-autosave plan done --quiet <n>` as each step (numbered from 1) finishes.

Record decisions with `agent-autosave note --quiet --type decision "..."` and dead ends with `agent-autosave note --quiet --type tried "..."`. They are proposed for this repo's memory.

If the agent-autosave tools are available, use them; otherwise run the commands.

Run each agent-autosave command on its own, never chained with other commands (`;`, `&&`, `||`), and always with `--quiet`.
