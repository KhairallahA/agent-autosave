# Cursor: capture and first-prompt injection (macOS, about 5 minutes)

**Who it's for:** Whoever runs the first real-install check of the Cursor adapter.

Throwaway repos under /tmp and the agent logins you already have; nothing
logs into anything new. Install from your checkout first, as in
[Install from a checkout](README.md#install-from-a-checkout).

## Run a session

```zsh
rm -rf /tmp/aa-cur && mkdir /tmp/aa-cur && cd /tmp/aa-cur && git init -q
# (no Codex here; Codex would need the folder trusted BEFORE init)
agent-autosave init --agents cursor,claude-code   # note: Cursor runs the Claude hooks; they are dropped
cursor .                                          # trust the workspace
```

In a Cursor agent chat: `Create add.py with add(a, b) returning a - b and test_add.py checking add(2, 2) == 4. Run python3 -m pytest -q test_add.py once and stop.`
Close the chat, wait 3 s, then:

```zsh
agent-autosave status                              # failing command: python3 -m pytest ...
grep -c '"agent": "claude-code"' .autosave/events.jsonl   # 0 (Cursor events recorded once, as cursor)
```

Open a **new** chat and send `continue`: the first reply should know the
failing test (save injected on the first prompt). To record real payloads, run `agent-autosave record on` before the chat and
add `.autosave/recordings/cursor/` as described in
[tests/fixtures/cursor/README.md](../../tests/fixtures/cursor/README.md).

## Undo

```zsh
cd /tmp/aa-cur && agent-autosave remove --all && cd / && rm -rf /tmp/aa-cur
```

## See also

- [Cursor adapter](../adapters/cursor.md)
- [Testing overview](README.md)
