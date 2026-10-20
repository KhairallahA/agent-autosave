# Claude Code fixtures

Real hook stdin from Claude Code 2.1.291, each wrapped in an envelope with
its provenance. Secrets and the home-directory prefix are redacted; no vendor
field was invented. A test that needs another value (for example a
StopFailure reason) changes one field of a real payload and says so.

- `payloads/` has one named payload per hook and tool. Together they cover
  all eight hooks. Load them with `tests.helpers.load_payload`.
- `sessions/lifecycle/` is one recorded run of 21 hooks across four
  sessions (three startups, `/clear`, `/compact`, three exits), named by
  hook and recording id. `test_claude_code_sessions.py` and
  `test_duplicates.py` replay it.

`SessionStart-startup`, `SessionStart-compact` and `SessionStart-clear` are
not stored twice. `tests.helpers.RECORDED` maps those names to their file in
`sessions/lifecycle/`.

Still wanted: StopFailure with `rate_limit` and PreCompact with `auto`. See
docs/testing/claude-code.md for how to record them.
