# Switching agents, the status line and watch

**Who it's for:** Anyone who runs more than one coding agent and wants to keep going when one hits its limit.

## go

```sh
agent-autosave go claude-code codex
```

`go` starts the first agent with the terminal handed straight to it. When
it exits, `go` reads the save. If the task was interrupted by a usage limit
or an overload, or the agent's last usage reading is at or above 98%, it
asks:

<!-- run: go -->
```console
$ agent-autosave go claude-code codex
Claude Code hit its usage limit at 03:15. Press Enter to continue in Codex, or q to stop.
Codex exited (0).
Run agent-autosave status.
```
<!-- end run -->

Enter runs the same path as `agent-autosave resume codex`, and the chain
goes on. A finished task ends it; so does quitting normally. With no
agents named, `go` uses `switch.order`, or the agents installed here in
install order. `--auto` continues after a 5 second countdown that any key
cancels. `agent-autosave resume --next` resumes in the agent after the one
that last worked on the task.

Above, Claude Code was a stand-in that hit its limit at once, and Codex
started with the save and exited.

## The Claude Code status line

`init` sets Claude Code's `statusLine` to `agent-autosave statusline` when
no settings layer has one:

```text
saved 4s ago | plan 2/5 | failing: pytest | 5h 72% | 7d 30% | ctx 41%
```

Claude Code runs it with a JSON payload on stdin. With one that carries
only a usage reading and the context window, in the task above:

<!-- run: statusline -->
```console
$ agent-autosave statusline
saved 4s ago | 5h 72% | ctx 41%
```
<!-- end run -->

Fields Claude Code does not send are left out. `statusline.style` can be
`plain` (the default) or `emoji`. When the usage reading moves 5 points or
more, it is recorded, so `go` and the handoff block know how much is left.
If you already have a status line, the wizard offers to wrap it: yours runs
first and ours is appended. `remove` puts your setting back byte for byte.
Codex's status line takes built-in items only, so there is none for Codex.

## watch

`agent-autosave watch` is a live screen refreshed every 2 seconds: status,
plan, the last action, the failing command, usage per agent, memory
candidates waiting for review, and every branch slot. `q` quits. `--once`
prints one screen:

<!-- run: watch-once -->
```console
$ agent-autosave watch --once
agent-autosave watch   updated 2026-10-15 03:15 UTC   (q quits)

Status    interrupted
Goal      Add a failing check for hello.py in tests/test_hello.py
Last      (nothing yet)
Failing   nothing
Memory    0 candidates waiting

Usage
  Claude Code 72% of 5-hour limit, context 41%

Slots
* main                         interrupted  t_20261015_031500000000
```
<!-- end run -->

## See also

- [Configuration](configuration.md)
- [MCP server](mcp.md)
- [Sharing with a team](sharing.md)
