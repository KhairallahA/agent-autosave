# Codex: record fixtures and check the handoff (macOS)

**Who it's for:** Whoever runs the manual Codex check on a real install before a release.

Takes about 15 minutes. Runs only in a throwaway repo under /tmp. Use the Codex
login you already have; nothing here logs into anything new. The payloads in
`tests/fixtures/codex/payloads/` are doc-derived until a recording replaces
them.

**Wait 3 seconds after every `/quit` before any check.** Codex hands the
terminal back before its SessionEnd hook has finished, so a check run at once
can read a SAVE.md from before the session ended (in a real run the check
failed right away and passed a few seconds later). `status`, `show` and
`resume` wait up to 1 second for this on their own; `cat` and scripts do not.

## 1. Install from your checkout

Follow [Install from a checkout](README.md#install-from-a-checkout), then:

```sh
codex --version               # the version you are recording
```

## 2. Make a scratch repo and trust it in Codex

```sh
rm -rf /tmp/aa-codex /tmp/aa-recordings
mkdir /tmp/aa-codex && cd /tmp/aa-codex && git init -q
codex
```

Codex asks whether you trust this folder: choose yes. Then type `/quit`.
Trusting first means `init` can use the project file `.codex/hooks.json`.
(Without trust, `init` skips Codex, writes nothing to `~/.codex/hooks.json`, and prints the
`config.toml` entry to add; trust the folder, then run `agent-autosave add codex`.)

## 3. Install the adapter

```sh
agent-autosave init --agents codex     # add ,claude-code to also check the Claude side (step 6)
cat .codex/hooks.json                  # 6 hooks, each "<abs path>/agent-autosave hook codex <Event>"
grep -c agent-autosave ~/.codex/hooks.json 2>/dev/null || echo "no user-level hooks"   # expect none: ONE layer only
cat AGENTS.md                          # the block; plan commands use the same absolute path as the hooks
```

Codex runs every hooks layer, so hooks in both files record every event twice
(the 2e6d0ed retest). `init` now writes one layer only and never adds a
second; if it prints a `duplicate_codex_hooks` warning, follow its hint:

```sh
python -m agent_autosave.installer.userhooks --codex --dry-run   # shows what would go
python -m agent_autosave.installer.userhooks --codex             # removes it, keeps a .agent-autosave-backup-<time>
```

If init prints `!!! WARNING: Codex hooks were NOT written to this repo`, an
older `~/.codex/hooks.json` has agent-autosave hooks (they may point at another
checkout): run the `userhooks --codex` fix it prints, `agent-autosave remove`,
then init again.

If the output says the hooks went to `~/.codex/hooks.json`, the folder was not
trusted. Stop, run the cleanup at the end, and redo step 2.

## 4. Turn on recording, start Codex and trust the hooks

```sh
agent-autosave record on --dir /tmp/aa-recordings --agent-version "$(codex --version)"
codex
```

Codex does not pass your shell environment to hooks, so the old
`AGENT_AUTOSAVE_RECORD*` exports recorded nothing. `record on` stores the
setting in `.autosave/config.json`, which every hook reads (the env vars still
work for Claude Code).

Codex warns that hooks need review. Type `/hooks`, check the six
agent-autosave entries are the commands from step 3, and trust them. Then type
`/quit` and start `codex` again in the same terminal, so this session runs
with the hooks trusted. Codex runs SessionStart at your **first prompt**, not
at launch (codex-rs queues it for the first turn), so a launch-and-quit only
records SessionEnd.

## 5. Drive one short session (paste these prompts one at a time)

1. `Use your update_plan tool to make exactly this 3-step plan: "Write hello.py", "Run it", "Add a failing check". Then do step 1 only: create hello.py that prints "hello autosave".`
2. `Run python3 hello.py. Then run python3 -c "raise SystemExit(7)" and stop there; do not fix it.`
3. Type `/compact` and wait for it to finish (PreCompact `manual`). Codex queues SessionStart `compact` for the next model request.
4. `Say ok.` (this turn fires SessionStart `compact`; without it no compact SessionStart is ever sent).
5. Type `/quit` (SessionEnd). Wait 3 seconds (Codex returns the terminal before the hook finishes), then `cat .autosave/SAVE.md` should say `paused`: SessionEnd settles the status in the hook itself.

Approve only these scratch actions when asked. Then:

```sh
ls ~/.codex/sessions/$(date +%Y/%m/%d)/      # the newest rollout-*.jsonl is this session
codex resume --last                           # SessionStart "resume" fires at the first prompt
```

Type `Say ok again.` (fires SessionStart with source `resume`), then `/quit` and wait 3 seconds.

## 6. Check the handoff

```sh
sleep 3                       # Codex's SessionEnd hook may still be writing
cat .autosave/SAVE.md         # status "paused", the plan (1 of 3 done), the exit-7 command, hello.py
cat .autosave/errors.log      # should not exist or be empty
python3 -m json.tool .autosave/install.json | grep -A4 '"hooks"'   # which layer each agent's hooks live in
cat .gitignore                # '# agent-autosave:start', '.autosave/', '# agent-autosave:end'
codex
```

In the new session ask: `What did the agent-autosave context at session start tell you?`
It should describe the unfinished task, the plan progress and the failing
`python3 -c "raise SystemExit(7)"` command. Then `/quit`.

If you also wired Claude Code in step 3 (`init --agents codex,claude-code`;
check `.claude/settings.json` has the 8 hooks), run `claude` here, ask the same
question, and check it names Codex as the previous agent. `/exit`.

If SAVE.md shows the exit-7 command as `exit 0`, or no failing command, say so
in your reply: the exit code is read from the rollout file (see
docs/adapters/codex.md, difference 3) and that is the main thing this run checks.

## 7. Optional: a real limit hit

Only if one happens naturally; do not burn quota to force it. Keep recording on
(`agent-autosave record on`, step 4) in a Codex session inside /tmp/aa-codex.
When Codex stops with a usage-limit message, `/quit`, then:

```sh
sleep 3                       # let the SessionEnd hook finish
cat .autosave/SAVE.md         # should say "interrupted" ... "hit a usage limit"
ls -t ~/.codex/sessions/*/*/*/rollout-*.jsonl | head -1     # that session's rollout
```

Keep that rollout path for step 8 (fixture `rollout-real-limit.jsonl`) and the
`Stop-*.json` / `SessionEnd-*.json` recorded right after the limit message.
If SAVE.md does not say interrupted, still keep the rollout: it shows what a
real limit looks like.

## 8. Scrub and copy the recordings

```sh
ls /tmp/aa-recordings/codex/
grep -RInE 'sk-|ghp_|xox[bp]-|AKIA|BEGIN .*PRIVATE|Bearer ' /tmp/aa-recordings || echo "no secret shapes"
grep -RIn "$HOME" /tmp/aa-recordings || echo "no home paths"    # recordings already show ~
```

Find which recording is which:

```sh
cd /tmp/aa-recordings/codex
grep -H '"source"' SessionStart-*.json
grep -lH '"tool_name": "Bash"' PostToolUse-*.json | xargs grep -H '"command"'
grep -lH '"tool_name": "apply_patch"\|"tool_name": "update_plan"' PostToolUse-*.json
```

Add the run as a recorded session. Point each named payload it covers at
its recording in `RECORDED` in `tests/helpers.py`, and delete a doc-derived
file from `payloads/` once a recording replaces it:

```sh
R=/tmp/aa-recordings/codex
D=tests/fixtures/codex
mkdir -p $D/sessions/<scenario>
cp $R/*.json $D/sessions/<scenario>/
python3 scripts/scrub-rollout.py ~/.codex/sessions/<yyyy/mm/dd>/rollout-<this session>.jsonl \
    $D/rollouts/rollout-<scenario>.jsonl
# only if step 7 happened:
# python3 scripts/scrub-rollout.py <limit rollout> $D/rollouts/rollout-real-limit.jsonl
```

The scrub script replaces your home path with `~`, redacts secrets, and drops
developer messages, instructions, turn context and reasoning. Open
the scrubbed rollout and the copied JSON files and check nothing
private is left (your prompts are the scratch prompts above):

```sh
grep -rn "$HOME\|$(whoami)" $D/sessions/<scenario> $D/rollouts || echo "clean"
```

Then run the tests:

```sh
python -m pytest -q tests/test_normalize_codex.py tests/test_codex_adapter.py
python -m pytest -q
```

Commit on a branch and open a pull request. If a test fails on the real
files, say so in the pull request: that is a real difference from the docs,
and finding it is the point of this run. Include what step 6 showed and
whether step 7 happened.

## Undo the scratch setup

```sh
cd /tmp/aa-codex && agent-autosave remove --all
grep -c agent-autosave ~/.codex/hooks.json 2>/dev/null || echo "no user-level hooks"   # expect 0 or none
rm -rf /tmp/aa-codex /tmp/aa-recordings
```

(Run `agent-autosave record off` first if you keep the folder.)

The trust entry for /tmp/aa-codex stays in `~/.codex/config.toml`
(`[projects."/private/tmp/aa-codex"]`). It is harmless; delete those two
lines if you want it gone.

## See also

- [Codex adapter](../adapters/codex.md)
- [Testing overview](README.md)
- [Resume tests](resume.md)
