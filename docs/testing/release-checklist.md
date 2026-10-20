# Release checklist

**Who it's for:** The maintainer, before tagging a release: the manual checks only real agents can do.

The automated suite replays recorded payloads. These checks use the real
apps, in throwaway folders under `/tmp`, with the Claude Code, Codex and
Cursor logins you already have. Every step gives a command and what to
expect. Stop at the first step that doesn't match, and keep its output for
the issue.

Each section is independent once step 1 is done. The commands are for zsh
on macOS; on Linux, skip the Homebrew and Obsidian steps.

## 1. The checkout

```zsh
cd <your checkout> && git pull --ff-only
source .venv/bin/activate && pip install -e '.[test]'
which agent-autosave && agent-autosave --version
```

Expect: `.../.venv/bin/agent-autosave` and the version being released.
Later steps write `<checkout>` for this folder.

## 2. The formula from a local Homebrew tap

```zsh
brew untap --force local/agent-autosave 2>/dev/null || true
rm -rf dist && uv build --out-dir dist
brew tap-new --no-git local/agent-autosave
python3 scripts/update-formula.py dist/agent_autosave-<version>.tar.gz \
  --url "file://$PWD/dist/agent_autosave-<version>.tar.gz" \
  --out "$(brew --repository)/Library/Taps/local/homebrew-agent-autosave/Formula/agent-autosave.rb"
brew install --build-from-source local/agent-autosave/agent-autosave
"$(brew --prefix)/bin/agent-autosave" --version
brew test local/agent-autosave/agent-autosave
brew audit --strict --formula local/agent-autosave/agent-autosave
brew style local/agent-autosave/agent-autosave
brew uninstall agent-autosave && brew untap local/agent-autosave
```

Expect:
- the version from Homebrew's copy;
- `brew test` passes, and `brew style` prints no offenses.

`brew audit` may print two notes about the URL: a `PyPiUrls` one, and one
saying a `file://` URL is not allowed. Both are expected for a local sdist.
Anything else is a finding.

## 3. The init wizard and doctor

```zsh
rm -rf /tmp/aa-rel && mkdir /tmp/aa-rel && cd /tmp/aa-rel && git init -q && git commit -q --allow-empty -m start
agent-autosave init
```

Expect, one screen at a time:
1. The agents found, with versions.
2. "Codex does not trust this folder yet". Choose **Open Codex here to
   trust it**, accept the trust prompt, then `/quit`.
3. Keep `.autosave/` out of git: **Yes**.
4. Record raw hook payloads: **No**.
5. The MCP tools: **Yes**.
6. The status line: **Yes** (or **Wrap it** if you have one).
7. Memory: **In .autosave/ in this repo**.
8. The summary of files. Enter writes them and shows "Ready.".

Then run `codex`. In it, run `/hooks`, trust the agent-autosave hooks, and
`/quit`. Wait 3 seconds, then:

```zsh
agent-autosave doctor
grep -c "agent-autosave done" CLAUDE.md AGENTS.md
```

Expect:
- "All green", with the Claude Code and Codex hooks, both blocks and both
  MCP registrations. No two-layer warning.
- `CLAUDE.md:0` and `AGENTS.md:0`: no block asks an agent to run `done`.

## 4. A Claude Code task, the status line and MCP

Run `claude`. If it asks about the project's `.mcp.json` server (`autosave`),
approve it. Ask:

`Make a 3-step plan with your todo tool: write stats.py with a mean() function, add tests/test_stats.py with a failing test for median(), then implement median(). Do steps 1 and 2 only, run pytest, and stop.`

Expect a status line like
`saved 4s ago | plan 1/3 | failing: pytest | 5h 12% | ctx 9%`. The 5h and
7d parts appear only if Claude Code sends your plan's limits. `/mcp` shows
`autosave` connected, with tools such as `autosave_status`. Then `/exit`.

## 5. Crash takeover

Run `claude` and ask: `Add a docstring to stats.py and stop.` While it
works, run this in a second terminal:

```zsh
kill -9 $(pgrep -nx claude)
cd /tmp/aa-rel && source <checkout>/.venv/bin/activate && claude
```

Expect: the new session's first message carries the
`[agent-autosave resume context]` block. It says Claude Code "was last
active at HH:MM and may still be running; the task is unfinished", and
names the goal and the plan. `/exit`.

## 6. `go claude-code codex` with a simulated limit

A real usage limit can't be forced. This step sends the hook Claude Code
sends at a limit (`StopFailure` with `error: "rate_limit"`) for the live
session.

```zsh
cd /tmp/aa-rel && agent-autosave go claude-code codex
```

In Claude Code, ask `Continue with step 3, then stop.` and wait until it's
idle. In a second terminal:

```zsh
cd /tmp/aa-rel && source <checkout>/.venv/bin/activate
SID=$(python3 -c 'import json; print(json.load(open(".autosave/save.json"))["last_session"]["session"])')
print -r -- "{\"session_id\":\"$SID\",\"transcript_path\":\"/tmp/none\",\"cwd\":\"$PWD\",\"hook_event_name\":\"StopFailure\",\"error\":\"rate_limit\"}" | agent-autosave hook claude-code StopFailure
agent-autosave status
```

Expect `Status  interrupted`. `/exit` Claude Code in the first terminal.
Expect `Claude Code hit its usage limit at HH:MM. Press Enter to continue in
Codex, or q to stop.`

Press Enter. Codex starts with a first prompt that begins
`[agent-autosave resume context]` and names the plan and the next step.
Let it finish, then `/quit`. Expect `Codex exited (0).`, and in
`agent-autosave status` a handoff `Claude Code → Codex · rate_limit`.

## 7. Tasks, history and the save

```zsh
agent-autosave done && agent-autosave status
```

Expect: `Task finished. The next prompt starts a new task.`, then `done`
with `Next  Nothing: the task is finished.`

Run `claude` and ask: `Write export.py that streams rows of a CSV to stdout. First run ls; cat export.py 2>/dev/null || find . -name export.py`. Then `/exit`.

```zsh
agent-autosave history
agent-autosave show --context --explain
agent-autosave show | grep -A6 "Where it stopped"
agent-autosave show | grep "Looked at"
```

Expect:
- `history` lists the stats task as `done`;
- `--explain` lists each section with its size, kept or dropped;
- no last action contains `/private/tmp` or `scratchpad` (they show as
  `<tmp>`), none is longer than about 100 characters, and the
  `ls; cat ...` command is not among them;
- `Looked at` lists the files Claude only read.

## 8. Branch slots and the compaction summary

```zsh
git add -A && git commit -qm wip && git checkout -qb feature && agent-autosave status
git checkout -q main 2>/dev/null || git checkout -q master; agent-autosave status --all
```

Expect: on `feature`, no task and no "may be stale" line. Back on main,
`--all` lists both slots.

Run `claude`. Ask `Summarise export.py in two sentences.`, then `/compact`,
then `/exit`. Then:

```zsh
agent-autosave show --md | grep -A2 "Session summary"
```

Expect: `## Session summary (Claude Code compacted its context: …)` followed
by the summary text.

## 9. Memory, recall and Cursor

```zsh
agent-autosave note "stats.py stays standard library only: no numpy" --type decision
agent-autosave done && agent-autosave memory review
```

Expect the decision listed as a candidate from `you`. Choose **Accept**.

Then check each of these:
1. `codex "Quote verbatim any [agent-autosave memory] block you were given with this prompt about stats.py and numpy, then stop."`
   quotes the decision.
2. In `claude`, `What does the agent-autosave project memory say?` quotes it.
3. Quit Cursor completely, run `agent-autosave add cursor && cursor .`, and
   in a new Agent chat ask `Quote any [agent-autosave memory] block you were given about stats.py.`
   Expect the decision, and no `invalid_args` message anywhere.
4. In a terminal, run `agent-autosave note --keep --type decision "Typed by me"`,
   then `agent-autosave memory review --json | grep -o '"agent": *"[a-z-]*"' | tail -1`.
   Expect `"you"`. When Claude Code, Codex or the Cursor agent runs the same
   note, expect its own name.

## 10. The vault in Obsidian

```zsh
mkdir -p /tmp/aa-vault
python3 - <<'PY'
import json, pathlib
p = pathlib.Path("/tmp/aa-rel/.autosave/config.json")
c = json.loads(p.read_text()); c["vault"] = {"path": "/tmp/aa-vault", "project": ""}
p.write_text(json.dumps(c))
PY
agent-autosave vault move --yes && ls -R /tmp/aa-vault/agent-autosave
```

Expect `✓ moved .autosave/MEMORY.md` and the journal, then `README.md` at
the top with `CURRENT.md`, `MEMORY.md` and `journal/` in the project folder.

In Obsidian, use **Open folder as vault** on `/tmp/aa-vault`:
- `agent-autosave/README.md` links to the save and to the memory.
- In reading view, `MEMORY.md` shows the decision with no `<!-- m:... -->`
  text.
- The journal page's `[[MEMORY#^m-...]]` link jumps to the bullet.

## 11. agentic-stack alongside

Install agentic-stack first, because its claude-code adapter overwrites
`CLAUDE.md` and `.claude/settings.json`:

```zsh
rm -rf /tmp/aa-stack /tmp/aa-stack-src
git clone -q https://github.com/codejunkie99/agentic-stack /tmp/aa-stack-src
mkdir /tmp/aa-stack && cd /tmp/aa-stack && git init -q && git commit -q --allow-empty -m start
/tmp/aa-stack-src/install.sh claude-code /tmp/aa-stack --yes
agent-autosave init --agents claude-code --yes && agent-autosave doctor
```

Expect an `agentic-stack` line in doctor, green. After
`claude "Write add.py with a function add(a, b). Then stop."`, check
`.agent/memory/working/WORKSPACE.md`. It should hold our block, with the
task as a quoted value and `- "add.py"` under `## Open files`.

## 12. Record any agent still marked "from docs"

```zsh
rm -rf /tmp/aa-rec && mkdir /tmp/aa-rec && cd /tmp/aa-rec && git init -q
agent-autosave init --agents <agent>
agent-autosave record on --agent-version "$(<agent command> --version)"
```

Start the agent and approve its hooks. Ask
`Create hello.py that prints hi, then run python3 -c "raise SystemExit(3)"`,
and quit. Then run `agent-autosave record off`, then
`python3 <checkout>/scripts/scrub-rollout.py --check .autosave/recordings/<agent>/*`.

Read every file before you commit it to `tests/fixtures/<agent>/recorded/`.
The recordings are redacted, but they are your real session.
[hook-agents.md](hook-agents.md) has each agent's details.

## 13. Before publishing

Merge the release branch into `main`, then follow
[releasing](../releasing.md#going-public-the-first-release-only). For the
first release, that means publishing a fresh export of `main` as a single
commit.

Clean up:

```zsh
rm -rf /tmp/aa-rel /tmp/aa-vault /tmp/aa-stack /tmp/aa-stack-src /tmp/aa-rec <checkout>/dist
```

## See also

- [Testing](README.md): what the automated suite covers
- [Releasing](../releasing.md): the steps after this checklist
