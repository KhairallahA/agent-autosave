# Install, doctor, remove and the registry (macOS, about 5 minutes)

**Who it's for:** Whoever checks the installers by hand before a release.

Throwaway repos under /tmp and the agent logins you already have; nothing
logs into anything new. Install from your checkout first, as in
[Install from a checkout](README.md#install-from-a-checkout). `tests/test_add_remove.py`, `tests/test_doctor.py`
and `tests/test_registry.py` cover the same ground without real agents.

## 1. Byte-exact remove and a red doctor

```zsh
rm -rf /tmp/aa-install && mkdir /tmp/aa-install && cd /tmp/aa-install && git init -q
# (no Codex in this step; for Codex, trust the folder in codex BEFORE init)
printf '# mine\n' > AGENTS.md; printf 'node_modules/\n' > .gitignore
shasum AGENTS.md .gitignore > /tmp/aa-install.sum
agent-autosave init --agents claude-code,generic
agent-autosave doctor                     # all ok (yellow warnings fine)
python3 - <<'PY'
import json; p='.claude/settings.json'; d=json.load(open(p)); del d['hooks']['Stop']; json.dump(d, open(p,'w'))
PY
agent-autosave doctor; echo "exit $?"     # red FAIL "Hook Stop is missing", hint: agent-autosave add claude-code, exit 1
agent-autosave add claude-code && agent-autosave doctor
agent-autosave remove claude-code         # CLAUDE.md and .claude/ gone, AGENTS.md block stays
agent-autosave remove --all
shasum -c /tmp/aa-install.sum && ls -a         # OK, OK; no .claude/.codex/CLAUDE.md left
```

## 2. Registry: shared Codex user-level hooks

First check `grep -o "hook codex" ~/.codex/hooks.json | wc -l`: if it is not 0, an
older install owns them; skip this step (init would write nothing).

```zsh
for r in one two three; do rm -rf /tmp/aa-$r && mkdir /tmp/aa-$r && (cd /tmp/aa-$r && git init -q && agent-autosave init --agents codex --user-level); done
cat ~/.config/agent-autosave/installs.json        # one hooks file, three installs
cd /tmp/aa-one && agent-autosave remove codex     # "Kept ~/.codex/hooks.json: other repos still use these hooks."
grep -o "hook codex" ~/.codex/hooks.json | wc -l   # still 6
rm /tmp/aa-three/.autosave/install.json           # three is "gone"
cd /tmp/aa-two && agent-autosave doctor           # warn: Stale registry entry /tmp/aa-three ... hint: agent-autosave doctor --prune
agent-autosave doctor --prune
agent-autosave remove codex                       # last live install: hooks reverted
grep -o "hook codex" ~/.codex/hooks.json | wc -l   # 0 (or no such file, if init created it)
```

## See also

- [Installing](../install.md)
- [Releasing](../releasing.md)
- [Testing overview](README.md)
