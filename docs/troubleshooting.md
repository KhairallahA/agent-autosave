# Troubleshooting

**Who it's for:** Anyone whose saves look wrong, late, doubled or missing.

Each section is a problem we hit while building agent-autosave against real
agents: what you see, why it happens, and the fix. Start with
`agent-autosave doctor`; most of these show up there.

## Every event is recorded twice

**You see:** `status` counts two turns for one prompt, or `SAVE.md` lists
each command twice.

**Why:** agents run hooks from every config layer they read. Claude Code
runs `~/.claude/settings.json` and the project's `.claude/settings.json`;
Codex runs `~/.codex/hooks.json` and the project's `.codex/hooks.json`. An
older install that wrote user-level hooks, plus a project install, means
every hook runs twice.

**What agent-autosave does:** it writes hooks to one layer only. If the
other layer already has agent-autosave hooks, `init` writes none and says so
first, in red:

<!-- run: layers-warning -->
```console
$ agent-autosave init --agents codex
agent-autosave init

  !!! WARNING: Codex hooks were NOT written to this repo: ~/.codex/hooks.json (user-level) already has 6 agent-autosave hook(s), and Codex runs every layer, so a second set would record every event twice. Codex in this repo will run the hooks from that file.
      Keep it, or for project-level hooks: python3 -m agent_autosave.installer.userhooks --codex --dry-run, then python3 -m agent_autosave.installer.userhooks --codex, then agent-autosave remove codex and agent-autosave add codex.

  – Claude Code         not installed, skipped
  = Codex 0.162.1       hooks not added: ~/.codex/hooks.json already has 6 agent-autosave hook(s)
  + AGENTS.md           agent-autosave block added
  = .codex/config.toml  MCP server already registered there; left as it is
  – Cursor              not installed, skipped
  – Gemini CLI          not installed, skipped
  – Antigravity         not installed, skipped
  – Copilot CLI         not installed, skipped
  – OpenCode            not installed, skipped
  + .autosave/          created and added to .gitignore

  Done. Your agents now autosave. Next:
    agent-autosave status    the save on one screen
    agent-autosave doctor    check every hook is in place
```
<!-- end run -->

That folder's Codex sessions use the user-level hooks a different project
installed earlier with `--user-level`:

<!-- run: layers-user-level -->
```console
$ agent-autosave init --agents codex --user-level
agent-autosave init

  – Claude Code         not installed, skipped
  + Codex 0.162.1       6 hooks in ~/.codex/hooks.json
  + AGENTS.md           agent-autosave block added
  = .codex/config.toml  MCP server already registered there; left as it is
  – Cursor              not installed, skipped
  – Gemini CLI          not installed, skipped
  – Antigravity         not installed, skipped
  – Copilot CLI         not installed, skipped
  – OpenCode            not installed, skipped
  + .autosave/          created and added to .gitignore

  ! In Codex, open /hooks and trust the 6 agent-autosave hooks.
    Codex skips new hooks until you do.

  Done. Your agents now autosave. Next:
    agent-autosave status    the save on one screen
    agent-autosave doctor    check every hook is in place
```
<!-- end run -->

**Fix:** keep one layer. To move to project-level hooks, strip ours from the
user-level file (it is backed up first, and only agent-autosave hooks are
removed), then add the agent again in each project:

```sh
python3 -m agent_autosave.installer.userhooks --codex --dry-run   # what it would remove
python3 -m agent_autosave.installer.userhooks --codex
agent-autosave remove codex && agent-autosave add codex
```

For Claude Code the same module works on `~/.claude/settings.json` without
`--codex`. `init` and `doctor` warn whenever both layers have our hooks.

## Codex records nothing

**You see:** after a Codex session, `status` shows no Codex turns and no
handoff.

**Why:** two Codex rules, both silent:

1. Codex runs project hooks (`.codex/hooks.json`) only in a folder it
   trusts. In an untrusted folder it ignores them. agent-autosave never
   falls back to user-level hooks on its own, because those run in every
   Codex session on the machine.
2. Codex skips hooks it has not seen before until you approve them in
   `/hooks`.

**Fix:** trust the folder (start `codex` there and accept the prompt), then
`agent-autosave add codex`, then in Codex open `/hooks` and trust the six
agent-autosave hooks. The `init` wizard offers to open Codex for the first
step; without the wizard, `init` skips Codex and prints the steps:

<!-- run: init -->
```console
$ agent-autosave init --yes
agent-autosave init

  + Claude Code 2.1.291  8 hooks in .claude/settings.json
  + CLAUDE.md            agent-autosave block added
  + Status line          agent-autosave statusline in .claude/settings.json
  + .mcp.json            MCP server registered (Claude Code asks you to approve it once)
  ! Codex 0.162.1        skipped: this folder is not trusted in Codex (no hooks written anywhere)
  – Cursor               not installed, skipped
  – Gemini CLI           not installed, skipped
  – Antigravity          not installed, skipped
  – Copilot CLI          not installed, skipped
  – OpenCode             not installed, skipped
  + .autosave/           created and added to .gitignore

  ! Codex was skipped: trust this folder in Codex first.
    To trust this folder, either start `codex` here and accept the trust prompt, or add to ~/.codex/config.toml:

        [projects."~/projects/hello"]
        trust_level = "trusted"

      e.g.  printf '%s\n' '' '[projects."~/projects/hello"]' 'trust_level = "trusted"' >> ~/.codex/config.toml
    Then run: agent-autosave add codex
    (Or, to put the hooks in ~/.codex/hooks.json for every Codex session: agent-autosave add codex --user-level)

  Done. Your agents now autosave. Next:
    agent-autosave status    the save on one screen
    agent-autosave doctor    check every hook is in place
```
<!-- end run -->

The `config.toml` entry is the one Codex writes when you accept its trust
prompt. If you would rather have the hooks in every Codex session, run
`agent-autosave add codex --user-level`.

## Status looks out of date right after quitting Codex

**You see:** you quit Codex and run `agent-autosave status` at once; it
still says `active`, or misses the last turn.

**Why:** Codex gives the terminal back before its SessionEnd hook has
finished, so the command can run before the save is rebuilt. (Claude Code
rebuilds in a detached background process after SessionEnd, with the same
effect.)

**What agent-autosave does:** `status`, `show`, `resume` and `doctor` look
for a `session_end` in the ledger that is newer than `save.json`; if there
is one, they wait up to one second for the hook to finish, then recompile
the ledger themselves. So the output is current even when the hook is late.

**If it still looks wrong:** run `agent-autosave save` to rebuild now, and
check `.autosave/errors.log` for a hook error.

A different kind of stale: if commits landed after the save, or it is more
than a day old (`inject.stale_after_hours`), the first line of `status` and
of the block an agent gets says *This save may be stale*. Trust git.

## Hooks do nothing when the agent starts from an IDE or desktop app

**You see:** sessions started from a terminal autosave; sessions started
from VS Code, Cursor or a desktop app do not, and `.autosave/errors.log` is
empty.

**Why:** apps started from the Dock or a launcher do not get your shell's
`PATH`, so a hook that runs plain `agent-autosave` is not found and the
agent drops the error.

**What agent-autosave does:** hooks call `agent-autosave` by absolute path
(the one found when you ran `init`), and `doctor` checks that the file is
still there. When it moves, for example after reinstalling in a new
virtualenv, `doctor` says so:

<!-- run: doctor-no-command -->
```console
$ agent-autosave doctor
agent-autosave doctor

  ✗ FAIL agent-autosave Hook command not found: ~/.local/bin/agent-autosave (moved or uninstalled?)
         hint: agent-autosave upgrade --yes
  ✓ ok   Claude Code    All 8 hooks present in .claude/settings.json.
  ✓ ok   Claude Code    CLAUDE.md block present.
  ✓ ok   memory         .autosave/MEMORY.md: 1 entry (limit 60).

  1 problem, 0 warnings.
```
<!-- end run -->

**Fix:** `agent-autosave upgrade --yes` rewrites every hook with the
current absolute path. Run it from the environment you want the hooks to
use.

## The resume block leaves something out

**Symptom:** a new session's block has no Checks, Git or Usage line, though
`SAVE.md` has it.

**Cause:** the block is packed into `inject.max_chars` (4,000 characters;
`resume` uses `inject.resume_max_chars`, 8,000). Whole sections are dropped
from the lowest priority up when it does not fit.

**Fix:** `agent-autosave show --context --explain` lists each section, its
size and whether it fit. Raise `inject.max_chars` (up to 12,000) in
`.autosave/config.json` if you want more.

## A new branch shows an empty save

**Symptom:** after `git checkout -b feature`, `status` says there is no task,
or a new session gets no resume block.

**Cause:** each branch has its own task. The other branch's save is parked,
not lost.

**Fix:** `agent-autosave status --all` lists every branch's task. Check the
branch out again to bring its save back, or read it from here with
`show --task <id>` or `resume <agent> --task <id>`.

## The new task started too early, or never

**Symptom:** a `/clear` started a new task while work was unfinished, or a
new request was folded into the finished one.

**Cause:** a clear starts a new task only when the task is finished: its
plan is all done, or the agent ran `agent-autosave done`.

**Fix:** run `agent-autosave done` when a task is finished (the blocks ask
agents to), or `agent-autosave clear` to start over by hand.

## Codex never asks for a handoff note

**Symptom:** Codex is near its limit but no handoff ask arrives.

**Cause:** the ask is made once per crossing of `handoff.warn_at_percent`
(85), on the next prompt, only when Codex reports its usage in the session
rollout, and not once a handoff note is recorded. `handoff.ask: false`
turns it off.

**Fix:** `agent-autosave status` shows the Usage line Codex last reported.
If there is none, Codex has not written usage for this session yet.

## Cursor shows "Choose a command or use --version."

**Symptom:** a Cursor session shows an `invalid_args` error with
"Choose a command or use --version."

**Cause:** Cursor runs a project's Claude Code hooks (Third-Party Imports)
and ignores their `args` list. Hooks written by earlier versions used `args`,
so Cursor ran `agent-autosave` with no subcommand.

**Fix:** `agent-autosave upgrade --yes` rewrites them as one command string.
The current version also exits silently when it gets a hook payload with no
subcommand.

## A note never shows up in project memory

**Symptom:** an agent recorded a decision, but `MEMORY.md` does not have it
and recall never brings it up.

**Cause:** notes are only proposed. They become candidates when the task
is finished or archived, and only you accept them.

**Fix:** `agent-autosave status` says how many candidates are waiting. Run
`agent-autosave memory review` to accept, edit, reject or skip each one.

## Recall adds nothing to a prompt

**Symptom:** a prompt about earlier work gets no `[agent-autosave memory]`
block.

**Cause:** recall needs at least 3 content words, and an entry must match
`recall.min_score` (0.34) of the prompt's terms. Each entry is shown once
per session, and expired entries never.

**Fix:** use the words the entry uses, or lower `recall.min_score`. Check
the entry with `agent-autosave memory list` or the page with
`agent-autosave journal --search WORD`.

## The journal is in .autosave/ though vault.path is set

**Symptom:** pages and `MEMORY.md` land in `.autosave/`, not the vault.

**Cause:** the vault folder is missing, not writable, or
`<vault>/agent-autosave` is not a plain folder. agent-autosave falls back to
`.autosave/` rather than lose a page.

**Fix:** `agent-autosave doctor` shows the reason in a yellow `vault` line.
Fix the folder, then run `agent-autosave vault move --yes` to move what
landed in `.autosave/`.

## The agent-autosave block left WORKSPACE.md

**Symptom:** `.agent/memory/working/WORKSPACE.md` has no agent-autosave
block, or the file is gone.

**Cause:** agentic-stack archives a `WORKSPACE.md` that is two days old
into `memory/episodic/snapshots/` when Claude Code stops.

**Fix:** nothing: the block is written again at the next turn. Set
`integrations.agentic_stack` to `off` to stop writing it.

## Something else

- `.autosave/errors.log` holds every hook and command error, redacted.
  Hooks never fail the agent, so problems land there instead of in your
  session.
- `agent-autosave doctor` checks each adapter, green, yellow or red, with a
  hint for each problem.
- To report a bug with the raw hook payloads, run `agent-autosave record
  on`, reproduce it, then `agent-autosave record off`. Recordings are
  redacted and stored under `.autosave/recordings/`.

## See also

- [FAQ](faq.md): shorter answers
- [Adapters](adapters/codex.md): per-agent limits
- [Contracts](contracts.md): every error code
- [Testing](testing/README.md): how to record a session for a bug report
