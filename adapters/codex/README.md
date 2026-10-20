# Codex adapter files

| File | Installed as | How |
| --- | --- | --- |
| `adapter.json` | — | the manifest: which file goes where, and how it is merged |
| `hooks.json` | `.codex/hooks.json` (or `$CODEX_HOME/hooks.json` with `--user-level`) | merged into the `hooks` key |
| `AGENTS.block.md` | `AGENTS.md` | a managed block, shared with the generic adapter |

Codex only runs project hooks in a folder it trusts. What each hook records,
trust, and the rollout reader: [docs/adapters/codex.md](../../docs/adapters/codex.md).
