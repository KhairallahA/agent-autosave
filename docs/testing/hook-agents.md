# Gemini CLI, Antigravity, Copilot CLI and OpenCode: record real payloads (macOS)

**Who it's for:** Whoever turns the from-docs adapters into recorded ones with a real install.

About 5 minutes per agent. Throwaway repos under /tmp and the logins you
already have; nothing here logs into anything new. Install from your
checkout first: [Install from a checkout](README.md#install-from-a-checkout).

## Record Gemini CLI hooks

```zsh
rm -rf /tmp/aa-gem && mkdir /tmp/aa-gem && cd /tmp/aa-gem && git init -q
agent-autosave init --agents gemini
agent-autosave record on --agent-version "$(gemini --version)"
gemini                      # approve the project hooks once, ask for a small change, run a failing test, /quit
agent-autosave record off
ls .autosave/recordings/gemini/     # SessionStart-..., BeforeAgent-..., AfterTool-..., SessionEnd-...
agent-autosave status       # the goal, the failing command, the last actions
```

Copy the recordings to `tests/fixtures/gemini/` and add a test that
replays them; then change the adapter's status to recorded.

## Antigravity, Copilot CLI and OpenCode

Same steps with `--agents antigravity` and `agy`, `--agents copilot` and
`copilot`, or `--agents opencode` and `opencode`. OpenCode's plugin builds
its own payload, so check `agent-autosave status` shows the tool commands.

## Resume

```zsh
agent-autosave resume gemini --dry-run    # gemini --prompt-interactive '[agent-autosave resume context] ...'
agent-autosave resume copilot --dry-run   # copilot -i '...'
```

## See also

- [Gemini CLI adapter](../adapters/gemini.md)
- [Antigravity adapter](../adapters/antigravity.md)
- [Copilot CLI adapter](../adapters/copilot.md)
- [OpenCode adapter](../adapters/opencode.md)
- [Testing overview](README.md)
