# Claude Code adapter files

| File | Installed as | How |
| --- | --- | --- |
| `adapter.json` | — | the manifest: which file goes where, and how it is merged |
| `hooks.json` | `.claude/settings.json` | merged into the `hooks` key; your hooks and settings stay |
| `CLAUDE.block.md` | `CLAUDE.md` | a managed block between `agent-autosave:start` and `:end` markers |

At install time, `agent-autosave` in the hooks and in the block's commands
becomes the absolute path of the installed executable. What each hook
records, and where it differs from Claude Code's docs:
[docs/adapters/claude-code.md](../../docs/adapters/claude-code.md).
