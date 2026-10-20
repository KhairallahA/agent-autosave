# Codex adapter

**Who it's for:** Codex users, especially anyone whose folder Codex does not trust yet.

## At a glance

| | |
| --- | --- |
| **Captured** | Prompts, Bash calls (exit code from the session rollout), `apply_patch` files, `update_plan` or a checklist in the reply, turn end, compaction, usage limits seen in the rollout, and session end. Six hooks in `.codex/hooks.json`. |
| **Loaded** | The SessionStart hook prints the resume block as `additionalContext`. The `AGENTS.md` block tells Codex where the save is. `agent-autosave resume codex` starts `codex` with the block as its first prompt. |
| **Limitations** | Codex runs project hooks only in a folder it trusts, and only after you approve them in `/hooks` ([troubleshooting](../troubleshooting.md#codex-records-nothing)). It runs hooks from every layer, so user-level and project hooks together record twice. Its terminal returns before SessionEnd finishes ([late SessionEnd](../troubleshooting.md#status-looks-out-of-date-right-after-quitting-codex)). |
| **Tested** | Yes, on a real install: codex-cli 0.162.1, macOS ([manual tests](../testing/codex.md)). Compaction, fork and rate-limit shapes are still doc-derived. |

Official hook reference: https://developers.openai.com/codex/hooks (read
2026-10-15). Wire formats were also checked against the codex-rs sources on
`main` (`hooks/src/schema.rs`, `hooks/src/events/*.rs`,
`core/src/tools/context.rs`, `rollout/src/policy.rs`, `protocol/src/protocol.rs`).

**Fixtures are real recordings where one exists** (codex-cli 0.162.1; see
`tests/fixtures/codex/README.md`).
SessionStart `compact`/`fork`, `update_plan` and the rate-limit rollouts are
still doc-derived (`"doc_derived": true`). No Codex runs on the build machine.

## What is wired

| Hook | Normalized event | Compiles the save? |
| --- | --- | --- |
| SessionStart | `session_start` (source, model, transcript) and prints the context block | yes, before recording the start |
| UserPromptSubmit | `prompt` (turn + 1) | yes |
| PostToolUse (`async`) | `tool` for Bash (exit code from the rollout), apply_patch (files from the patch headers) and other tools (name only); `plan` for `update_plan` | only for a failing Bash call |
| PreCompact | `compact` (trigger) | yes |
| Stop | `plan` (origin `message`) from a `- [ ]` checklist when `update_plan` never ran, `turn_end`, then `interrupted` if the rollout tail shows a limit | yes |
| SessionEnd | `interrupted` (if the limit was not reported at Stop), then `session_end` | detached background rebuild, as for Claude Code |

Every hook is a command string: `<absolute path of agent-autosave> hook codex <Event>`
(shell-quoted). The entrypoint is the same as Claude Code's: at most 2 MiB of
stdin, errors go to `.autosave/errors.log`, nothing is printed except the
SessionStart JSON (`hookSpecificOutput.additionalContext`), exit code is always 0.
Stop prints nothing, which the docs treat as success.

Per-session turn counters, the "a to-do tool ran" flag and the last turn whose
limit was reported live in `.autosave/adapter-codex.json` (last 20 sessions).

## Install

- `init` detects Codex (`codex` on PATH, or `$CODEX_HOME`, default `~/.codex`).
- **Trusted folder** (`[projects."<path>"] trust_level = "trusted"` for the
  folder or a parent in `$CODEX_HOME/config.toml`, read-only): hooks are merged
  into `<repo>/.codex/hooks.json`.
- **Not trusted:** Codex is **skipped**; nothing is written to
  `$CODEX_HOME/hooks.json`. The other agents are installed, and init prints
  the exact entry to add to `$CODEX_HOME/config.toml`:

      [projects."/abs/path/to/repo"]
      trust_level = "trusted"

  with a `printf ... >> config.toml` command that appends it (or start `codex`
  in the folder and accept the trust prompt), then `agent-autosave add codex`.
  JSON: warning `codex_untrusted_skipped`; with Codex as the only agent, exit 1
  `codex_untrusted` and nothing is written. `add codex` follows the same rule.
  There is no silent fallback to the user-level file: hooks there run in
  every Codex session on the machine.
- **`--user-level` (explicit only):** hooks go to `$CODEX_HOME/hooks.json`.
  User-level hooks run in every repo but stay silent where there is no
  `.autosave/`. Repairing (`add codex`) or `upgrade --yes` of such an install
  also needs `--user-level`.
- Both: an `AGENTS.md` managed block (same text as the CLAUDE.md block) and the
  `.gitignore` block. Nothing else is written; a test checks other agents'
  files are untouched.
- Every insertion is recorded in `.autosave/install.json`. A user-level file is
  recorded with its absolute path and `"scope": "user"`; `remove` accepts an
  absolute path only if it is exactly `$CODEX_HOME/hooks.json`.
- **Which layer.** install.json `hooks.codex` records `layer` (`project` or
  `user`), `path` and `added`; when init found our hooks in a layer and added
  none, `added: false` with `existing_hooks`. init then prints a
  `!!! WARNING: Codex hooks were NOT written to this repo` line at the top
  (JSON: `codex_hooks_in_other_layer`), because Codex in this repo runs those
  hooks, which may belong to another checkout or venv.
- **One layer only.** Codex runs hooks from every layer, so hooks in both
  files record every event twice (seen in a real run: 28 twin pairs,
  ~1 ms apart). init writes the project file when the folder is trusted, the
  user file only with `--user-level`, and writes neither if either already holds
  agent-autosave hooks. If both do, every init run warns
  `duplicate_codex_hooks` with the hint:
  `python -m agent_autosave.installer.userhooks --codex --dry-run`, then
  without `--dry-run` (or `--file <repo>/.codex/hooks.json` when untrusted).
  It keeps a `hooks.json.agent-autosave-backup-<time>` copy.
- **Plan CLI path.** The AGENTS.md block uses the same absolute, shell-quoted
  command as the hooks (a plain `agent-autosave plan set` failed with
  exit 127 in a real run: it was not on Codex's PATH). Real Codex 0.162.1 sessions may lack
  `update_plan`; the block then says to use the plan CLI, or end the reply
  with a `- [ ]` checklist, which Stop reads.
- **Hook review.** Codex skips new or changed non-managed hooks until they are
  trusted in `/hooks`. init prints this; the manual test guide includes the step.

## When SessionStart fires (recordings, docs and codex-rs)

Codex started four times in a recorded run but sent one SessionStart per
session that got a prompt. That matches the source:

- `core/src/session/session.rs` picks the source from the initial history
  (`New` → startup, `Resumed` → resume, `Forked` → fork, or resume without a
  fork parent, `Cleared` → clear) and only **queues** it
  (`queue_pending_session_start_source`).
- `core/src/session/mod.rs` queues `compact` after a compaction is persisted.
- `core/src/hook_runtime.rs` `run_pending_session_start_hooks` runs the queue,
  called from `core/src/session/turn.rs` when a turn starts (and after a
  mid-turn auto-compaction). So **SessionStart runs at the first prompt of a
  session, never at launch.** The recordings agree: each SessionStart is
  ~100 ms before that session's first UserPromptSubmit.
- The docs say the same for compact: "After Codex compacts a root session,
  `SessionStart` hooks that match `source: "compact"` run before the next model
  request" (developers.openai.com/codex/hooks, read 2026-10-15).

So: a launch to trust hooks then `/quit` sends only SessionEnd; `/compact` then
`/quit` sends PreCompact and SessionEnd, never SessionStart `compact`;
`codex resume --last` then `/quit` sends only SessionEnd (same session id);
resume plus a prompt sends SessionStart `resume`. Adaptations:

- Compact state is never cleared by SessionStart `compact` alone: the next
  prompt, Stop or SessionEnd clears it (SessionEnd → paused).
- SessionEnd settles the status in the hook itself (in-process, no git; Codex
  kills each hook's process group when it returns or times out, codex-rs
  `hooks/src/engine/command_runner.rs` `ProcessTreeGuard`, so the detached
  rebuild may never land). So `/compact` + `/quit` ends `paused`, as with
  Claude Code, not `interrupted (compact)`.
- A resumed session reuses its session id, so its next event reopens it
  (`last_session.ended` turns false). A SessionEnd-only session still pauses
  the task (harmless: the next prompt makes it active).
- The context is still injected at SessionStart; under Codex that is the first
  prompt's turn, so the model sees it before answering.
- `PostCompact` exists (docs and `hooks/src/schema.rs`) and is not wired: the
  rules above do not need it.

## Implementation choices

1. **SessionEnd is a sixth hook.** Besides the five core events, current Codex has
   SessionEnd (reason is always `other`; 1 s default timeout, 3 s max). It is
   wired with `timeout: 3` so a Codex save becomes `paused` on exit, as with
   Claude Code. Interrupt, PostCompact, PreToolUse, PermissionRequest and the
   subagent hooks are not wired.
2. **Trust is two-level.** The plan covers folder trust only. The docs add a
   per-hook review: each hook is trusted by hash in `/hooks`, and changed hooks
   are skipped until reviewed again. `init` cannot do this for the user.
3. **Exit codes are not in the hook payload.** For Bash, `tool_response` is
   the command's output text only (codex-rs `ExecCommandToolOutput`). The hook
   reads the rollout tail (`transcript_path`, last 256 KiB) for the call's
   `function_call_output` (`Process exited with code N`) or a paginated
   `item_completed` `CommandExecution.exit_code`, retrying for up to 120 ms
   because PostToolUse is async. If the code is not found, the call is recorded
   with `exit: null` (unknown): shown as "exit unknown", never counted as a pass
   or a failure, it never resolves an earlier failure and it blocks `done` until
   the command runs again with a known code (the plan's compiler rules). **Needs a real recording** to confirm the output line is written
   before the hook reads it.
4. **Rate limit.** Codex has no failure hook, as the plan says. Transient `error`
   events are not persisted to the rollout (rollout policy), so the reader
   uses, for the newest turn only (after the last `task_started`):
   `task_complete.error.codex_error_info` = `usage_limit_exceeded` /
   `rate_limit_exceeded` (also `server_overloaded` to overloaded, `unauthorized`
   to auth), or the last `token_count.rate_limits` with
   `rate_limit_reached_type` set or a window at `used_percent >= 100`. It runs
   at Stop and again at SessionEnd (in case Stop does not fire on a failed
   turn), reporting each limited turn once. The plan asks for a real limit hit
   before the parser; this one is written from the source and **must be
   checked against a real limit hit** ([docs/testing/codex.md](../testing/codex.md) step 7).
5. **`.zst` rollouts.** Codex writes the live rollout as plain `.jsonl` and
   compresses only cold files (rewritten as plain when appended to), so hooks
   should never see `.zst`. Support is there anyway: Python 3.14's stdlib
   `compression.zstd`, else the `zstd` binary (150 ms timeout), else skipped.
6. **SessionStart `fork`** (new source) is recorded as `resume`; the event
   schema keeps its four sources.
7. **Plan tool.** `update_plan` (`plan: [{step, status}]`, statuses
   `pending/in_progress/completed`) gives full-list `plan` events, like TodoWrite.
8. **Hook command is a string**, as the plan shows, but with the absolute,
   shell-quoted path of the console script (same reason as Claude Code), plus
   `timeout` on every hook and `async: true` on PostToolUse.
9. **Codex home.** `$CODEX_HOME` is honoured for detection, trust, the
   user-level file and removal.

## Open questions

- Whether Stop fires for a turn that fails on a usage limit (affects only
  timing: SessionEnd catches it otherwise).

## Shared user-level hooks

Two repos installed with `--user-level` share `$CODEX_HOME/hooks.json`. Each
repo using it is listed in the registry
`$XDG_CONFIG_HOME/agent-autosave/installs.json` (default
`~/.config/agent-autosave/installs.json`) with repo, agent and date, plus the
recorded insertion. `agent-autosave remove codex` keeps the hooks while
another live install uses them and reverts them byte for byte with the last
one. A repo without `.autosave/install.json` counts as gone; `doctor` lists
it and `doctor --prune` drops it.

## Recording mode

Codex does not pass the shell environment to hooks, so use
`agent-autosave record on --dir <dir> [--agent-version V]` (stored in
`.autosave/config.json` under `record`; `record off` stops). Otherwise the
same as Claude Code: `AGENT_AUTOSAVE_RECORD=1` (when it reaches the hook) saves each raw payload (home
path shown as `~`, secrets redacted) to `$AGENT_AUTOSAVE_RECORD_DIR/codex/` or
`.autosave/recordings/codex/`.

## Repair and remove

`agent-autosave doctor` checks every hook event, the block and the hook command (red with a fix). `agent-autosave add codex` re-adds only what is missing. `agent-autosave remove codex` reverts only its own insertions, byte for byte; `remove --all` takes every agent out.

## Adding Codex

In a trusted folder:

<!-- run: add-codex -->
```console
$ agent-autosave add codex
Added Codex: .codex/hooks.json, AGENTS.md, .codex/config.toml
In Codex, open /hooks and trust the agent-autosave hooks.
```
<!-- end run -->

When Codex's hooks are already in `~/.codex/hooks.json`, project hooks would record every event twice, so `init` leaves them out and says why:

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

## See also

- [Claude Code adapter](claude-code.md)
- [Manual tests](../testing/codex.md): the real-install checklist
- [Troubleshooting](../troubleshooting.md#codex-records-nothing): Codex records nothing
- [Contracts](../contracts.md): `codex_untrusted` and `user_level_needed`
