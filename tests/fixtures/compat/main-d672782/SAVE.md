<!-- agent-autosave v:1 -->
# agent-autosave — active task

**Status:** active, 2026-10-17T21:03:29.126297Z
**Goal:** Reply OK. This scratch request records an authentication failure using a deliberately invalid test credential.
**User steering:** None.

## Where it stopped
- Ran python3 -c "raise SystemExit(7)" (exit 7)
- Ran python3 hello.py (exit 0)

## Failing
`python3 -c "raise SystemExit(7)"` (exit 7)

    Exit code 7

## Files and git
Branch master, no commits yet.
Files: None recorded.

## Decisions
None recorded.

## Next action
Make `python3 -c "raise SystemExit(7)"` pass.

## Before you continue
Run `git status` and confirm the files above match. If they don't, trust git over this save.
