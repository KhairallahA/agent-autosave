# Cursor adapter

**Who it's for:** Cursor users; note that this adapter has not been tested on a real install.

## At a glance

| | |
| --- | --- |
| **Captured** | Session start, prompts, Shell calls with exit codes, file edits, compaction, stop (errors as `interrupted`), session end. Seven hooks in `.cursor/hooks.json`. |
| **Loaded** | The first prompt of each conversation gets the resume block as `additional_context`. An always-apply rule in `.cursor/rules/agent-autosave.mdc` points at the save. `resume cursor` cannot start Cursor with a prompt; paste `agent-autosave show --context`. |
| **Limitations** | Project hooks run only in a trusted workspace. Cursor also runs the Claude Code hooks; those events are dropped so a session is recorded once. |
| **Tested** | **Untested on a real install.** Built from Cursor's docs and doc-derived fixtures. |

**Untested on a real install:** no manual run has covered Cursor yet;
everything below is from Cursor's docs and doc-derived fixtures.

Hooks in `.cursor/hooks.json` (project level; Cursor runs project hooks from
the project root, only in a trusted workspace) plus an always-apply rule in
`.cursor/rules/agent-autosave.mdc` as the fallback. Shapes follow
cursor.com/docs/hooks (checked 2026-10-15). The fixtures in
`tests/fixtures/cursor/` are doc-derived; real recordings are pending
(`agent-autosave record on`, then use Cursor).

## What is wired

| Cursor event | Recorded as | Notes |
|---|---|---|
| `sessionStart` | `session_start` | No context output: it misses the first message in the Agents Window. |
| `beforeSubmitPrompt` | `prompt` | Always prints `{"continue":true}`. On the **first prompt of each conversation** it adds `additional_context` with the resume block (if the save is unfinished). |
| `postToolUse` (matcher `Shell`) | `tool` | Exit code from `tool_output` JSON (`exitCode`); the last 20 output lines on failure. Other tools are ignored. |
| `afterFileEdit` | `tool` (Write) | `file_path`, relative to the first workspace root. |
| `preCompact` | `compact` | `trigger` auto/manual. |
| `stop` | `turn_end`, or `interrupted` (`unknown`) when `status` is `error` | |
| `sessionEnd` | `session_end` | Settles the status in-process, like Codex. |

The conversation id is the session. `afterShellExecution` is not wired (no
exit code). All hooks have `timeout: 5` and never block: any error is logged
to `.autosave/errors.log`, and `beforeSubmitPrompt` still prints
`{"continue":true}`.

## Claude Code hooks inside Cursor

Cursor also runs `.claude/settings.json` hooks ("Third-Party Imports", on by
default). Those payloads carry `cursor_version`; the claude-code hook drops
them, so a Cursor session is recorded once, as `cursor`. `init` and `doctor`
say so. To stop Cursor running them, turn off Third-Party Imports.
Cursor runs only each hook's `command` string and ignores an `args` list, so
the Claude Code hooks are written as one string. A run of `agent-autosave`
with no subcommand and a JSON payload on stdin (an older install's hook)
exits 0 silently; run `agent-autosave upgrade --yes` to rewrite those hooks.

## Install and remove

`agent-autosave init --agents cursor` or `agent-autosave add cursor`.
`.cursor/hooks.json` is merged (insertion only; your hooks stay first);
the rule file is created and deleted by `remove cursor` (and the
`.cursor/rules/` folder if we created it and it is empty). Trust the folder in
Cursor.

## Resume

Cursor has no CLI that takes a first prompt: `agent-autosave resume cursor`
exits 2 (`not_supported`) and says to paste `agent-autosave show --context`
as your first message. Usually not needed: the first prompt of a new chat
gets the save automatically.

## Resuming in Cursor

Cursor cannot be started with a prompt, so `resume cursor` says what to do instead:

<!-- run: error-resume-cursor -->
```console
$ agent-autosave resume cursor
error: Cursor cannot be started with a first prompt.
hint: Paste the output of `agent-autosave show --context` as your first message, or ask the agent to read .autosave/SAVE.md.
```
<!-- end run -->

## See also

- [Manual tests](../testing/cursor.md): the checklist for a first real run
- [Claude Code adapter](claude-code.md): why Cursor's copies of Claude hooks are dropped
- [Troubleshooting](../troubleshooting.md)
