# GitHub Copilot CLI adapter

**Who it's for:** GitHub Copilot CLI users. The hooks follow GitHub's hooks reference and have not been recorded on a real install yet.

## At a glance

| | |
| --- | --- |
| **Captured** | Session start and end, prompts, tool calls and failures, turn ends and compaction. |
| **Loaded** | The save is given at `sessionStart` (top-level `additionalContext`). Copilot drops prompt-hook output, so recall is not added to prompts. `agent-autosave resume copilot` runs `copilot -i "<block>"`. |
| **Status** | **From docs**: shapes from <https://docs.github.com/en/copilot/reference/hooks-reference>; not recorded yet. |

## What init writes

`init --agents copilot` (or `add copilot`) creates
`.github/hooks/agent-autosave.json` with seven hooks under their PascalCase
names, so payloads arrive in the "VS Code compatible" snake_case format,
and shares the `AGENTS.md` block with Codex. `remove copilot` deletes the
hooks file and the block.

Copilot CLI also runs the hooks in `.claude/settings.json`. Those calls
carry a `timestamp` field, which Claude Code never sends, so the Claude Code
hook drops them and nothing is recorded twice.

## See also

- [Recording the hook agents](../testing/hook-agents.md)
- [Claude Code adapter](claude-code.md)
- [Generic adapter](generic.md)
