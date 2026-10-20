# Antigravity adapter

**Who it's for:** Antigravity users (app, CLI and IDE). The hooks were recorded on a real install: antigravity-cli 1.3.5 and 1.4.2 on macOS.

## At a glance

| | |
| --- | --- |
| **Captured** | Session start, every tool call (`PostToolUse`), and the end of each run (`Stop`). Antigravity has no hook with the prompt text or the agent's last message, so the goal comes from the plan and notes. |
| **Loaded** | The save is given at `SessionStart` as an injected system message (`injectSteps`). `agent-autosave resume antigravity` runs `agy --prompt-interactive`. |
| **Status** | **Recorded**: antigravity-cli 1.3.5 and 1.4.2 (`tests/fixtures/antigravity/recorded/`); shapes match <https://antigravity.google/docs/hooks>. |

## What init writes

`init` (when `agy` is on PATH, or `--agents antigravity`) and `add
antigravity` add one hook definition named `agent-autosave` to
`.agents/hooks.json`, and a short managed block to `ANTIGRAVITY.md`. Your
other hook definitions in that file are left alone; `remove antigravity`
restores it byte for byte.

## What the recordings show

- Payloads carry no event name. Ours comes from the command line.
- **No exit code.** `PostToolUse` has `toolCall` (`name`, `args`), `stepIdx`
  and `error`, and no output. For `python3 -c "raise SystemExit(3)"`,
  `error` was `""`. So agent-autosave records a command's exit as
  **unknown**, never as a pass. The save says "(exit unknown)" and asks for
  the command to be re-run. A non-empty `error` counts as a failure: an
  "exit status N" text gives exit N, and any other text gives 1.
- **No prompt and no last message.** `SessionStart` has `conversationId`,
  `workspacePaths`, `transcriptPath`, `artifactDirectoryPath` and
  `modelName`. `Stop` adds `terminationReason`, `executionNum`, `fullyIdle`
  and `error`. Neither the prompt nor the agent's reply is there.
- Usage limits are not in any hook.
- `transcriptPath` points into Antigravity's own data folder
  (`~/.gemini/antigravity-cli/brain/<conversation>/`). agent-autosave never
  reads it.

## See also

- [Recording the hook agents](../testing/hook-agents.md)
- [Gemini CLI adapter](gemini.md)
- [Generic adapter](generic.md)
