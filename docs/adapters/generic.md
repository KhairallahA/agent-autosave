# Generic adapter

**Who it's for:** Users of any agent not listed, as long as it reads `AGENTS.md` and runs shell commands.

## At a glance

| | |
| --- | --- |
| **Captured** | Only what the agent records itself by following the `AGENTS.md` block. |
| **Loaded** | The agent reads `AGENTS.md`, which points at `.autosave/SAVE.md`. `resume generic` cannot start an unknown agent; paste `agent-autosave show --context`. |
| **Limitations** | No hooks; never auto-detected. |
| **Tested** | Not applicable: it is a text block for any agent. |

For any agent that reads `AGENTS.md` and can run shell commands. Never
auto-detected: `agent-autosave init --agents generic` or `add generic`.

It adds the same `AGENTS.md` block as Codex (one block, shared). The block
has a paragraph for agents without hooks: read `.autosave/SAVE.md` first, run
`agent-autosave save --quiet` after each piece of work, and record plans and
decisions with `plan` and `note`. The block is owned by every agent that
added it (`agents` in install.json): `remove codex` keeps it while `generic`
is installed, and removing the last of them restores `AGENTS.md` byte for
byte.

`agent-autosave resume generic` exits 2 (`not_supported`): paste
`agent-autosave show --context` as the first message, or ask the agent to
read `.autosave/SAVE.md`.

## See also

- [Gemini CLI adapter](gemini.md) and [Antigravity adapter](antigravity.md): named instruction blocks
- [Contracts](../contracts.md): the commands the block asks the agent to run
- [Recording the hook agents](../testing/hook-agents.md)
