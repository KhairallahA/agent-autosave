# Cursor adapter files

| File | Installed as | How |
| --- | --- | --- |
| `adapter.json` | — | the manifest: which file goes where, and how it is merged |
| `hooks.json` | `.cursor/hooks.json` | merged into the `hooks` key; your hooks run first |
| `agent-autosave.mdc` | `.cursor/rules/agent-autosave.mdc` | created, and deleted by `remove cursor` |

Details: [docs/adapters/cursor.md](../../docs/adapters/cursor.md).
