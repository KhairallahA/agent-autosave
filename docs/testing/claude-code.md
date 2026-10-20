# Claude Code: record fixtures and check the handoff (macOS)

**Who it's for:** Whoever runs the manual Claude Code check on a real install before a release.

Takes about 10 minutes. Runs only in a throwaway repo under /tmp. Nothing here
logs into anything new: use the Claude Code login you already have.

## 1. Install from your checkout

Follow [Install from a checkout](README.md#install-from-a-checkout), then:

```sh
claude --version              # the version you are recording
```

The hooks store the absolute path of this venv's `agent-autosave`, so they
work from any terminal, the desktop app or an IDE.

## 2. Make a scratch repo and install the adapter into it

```sh
rm -rf /tmp/aa-scratch /tmp/aa-recordings
mkdir /tmp/aa-scratch && cd /tmp/aa-scratch && git init -q
# If Codex is installed: run `codex` here first and trust the folder (then /quit);
# init skips Codex in untrusted folders and prints how to trust it.
agent-autosave init
cat .claude/settings.json     # 8 hooks, each with the absolute path of agent-autosave
```

## 3. Turn on recording and start Claude Code

```sh
agent-autosave record on --dir /tmp/aa-recordings --agent-version "$(claude --version)"
claude
```

Accept the trust prompt for /tmp/aa-scratch. This first start records
SessionStart with source `startup`.

## 4. Drive one short session (paste these prompts one at a time)

1. `Use your todo/task tool to make a 3-step plan: write hello.py, run it, add a failing check. Then do step 1 only: write hello.py that prints "hello autosave".`
2. `Run python3 hello.py. Then run python3 -c "raise SystemExit(7)" and stop there; do not fix it.`
3. Type `/compact` and wait for it to finish (records PreCompact `manual` and SessionStart `compact`).
4. Type `/clear` (records SessionStart `clear`).
5. Type `/exit` (records SessionEnd).

Approve only these scratch actions when asked.

## 5. Check the handoff

```sh
cat .autosave/SAVE.md         # status "paused", the plan, the exit-7 command
claude
```

In the new session, ask: `What did the agent-autosave context at session start tell you?`
It should describe the unfinished task, the plan progress and the failing
`python3 -c "raise SystemExit(7)"` command. Then `/exit`.

Also run `cat .autosave/errors.log` (it should not exist or be empty).

## 6. Optional: a real rate limit

Only if one happens naturally; do not burn quota to force it. Keep recording on
(`agent-autosave record on`, step 3) in a session inside /tmp/aa-scratch.
When Claude Code stops with a usage-limit message, a
`/tmp/aa-recordings/claude-code/StopFailure-*.json` file appears. Check its
`payload.error` is `rate_limit`. Auto-compaction similarly records PreCompact
with `trigger: "auto"` if it ever fires.

## 7. Review and push the recordings

```sh
ls /tmp/aa-recordings/claude-code/
grep -RInE 'sk-|ghp_|xox[bp]-|AKIA|BEGIN .*PRIVATE|Bearer ' /tmp/aa-recordings || echo "no secret shapes"
```

Open the files you want to keep and check nothing private remains (prompts
are the scratch prompts above). Add them as a recorded session, and give the
named payloads that were missing an entry in `RECORDED` in `tests/helpers.py`:

```sh
D=tests/fixtures/claude-code
mkdir -p $D/sessions/<scenario>
cp /tmp/aa-recordings/claude-code/*.json $D/sessions/<scenario>/
grep -H '"source"' $D/sessions/<scenario>/SessionStart-*.json   # which start is which
python -m pytest -q
```

Commit on a branch and open a pull request with the agent version and what
step 5 showed.

## Undo the scratch setup

```sh
cd /tmp/aa-scratch && agent-autosave remove --all
rm -rf /tmp/aa-scratch /tmp/aa-recordings
```

(`agent-autosave record off` stops recording if you keep the folder. The old
`AGENT_AUTOSAVE_RECORD=1` exports still work too.)

## See also

- [Claude Code adapter](../adapters/claude-code.md)
- [Testing overview](README.md)
- [Resume tests](resume.md)
