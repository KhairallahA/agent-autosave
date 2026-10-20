# Codex fixtures

Recordings come from codex-cli 0.162.1 with the home directory shown as `~`
and secrets redacted. Each recording is named by its hook and recording id.

| Path | What it is | Read by |
| --- | --- | --- |
| `sessions/plan-handoff/` | 18 recordings of one run: plan + `hello.py`, a failing command (exit 7), `/compact`, `/quit`, then a second session that picks up the save, and a launch-and-quit session that only sent SessionEnd | `test_codex_layers.py` |
| `sessions/resume-quit/` | 19 recordings plus the run's ledger (`events.jsonl`): `/quit`, `codex resume --last`, a second `/quit`. The two SessionEnds and two "ok" Stops are separate quits and turns (different `turn_id`), not repeats | `test_session_end.py` |
| `ledgers/two-hook-layers.jsonl` | a ledger with hooks in both `.codex/hooks.json` and `~/.codex/hooks.json`, so every Codex event appears twice | `test_codex_adapter.py`, `test_plan_cli.py` |
| `ledgers/passing-run/` | the ledger and `save.json` of a run whose last command passed | `test_commands.py` |
| `rollouts/rollout-real-session.jsonl` | the scrubbed rollout of the two-layer run | `test_codex_adapter.py`, `test_normalize_codex.py`, `test_codex_layers.py` |
| `rollouts/rollout-2026-10-15T10-00-00-*.jsonl` | synthetic rollout tails for the exit-code lookup and rate-limit detection. The `limit-*` ones are not a real limit hit | `test_normalize_codex.py`, `test_codex_adapter.py`, `test_performance.py` |
| `payloads/` | payloads with no recording yet, marked `"doc_derived": true` | `test_normalize_codex.py` |

Named payloads (`Stop`, `PostToolUse-Bash`, `SessionStart-resume` and the
rest) are not copied out of the sessions. `tests.helpers.RECORDED` maps each
name to its recording; `load_payload("codex", name)` reads it.

The doc-derived payloads are `SessionStart-compact` (Codex sends it only at
the next prompt after `/compact`, which no recorded run reached),
`SessionStart-fork` and `PostToolUse-update_plan` (0.162.1 sessions had no
`update_plan` tool). They follow the Codex hooks documentation
(https://developers.openai.com/codex/hooks, read 2026-10-15) and the codex-rs
sources. Replace them with recordings as described in docs/testing/codex.md.

## Scrubbing

`rollouts/rollout-real-session.jsonl` was rewritten by `scripts/scrub-rollout.py` with
`--keep-prompt` for the two scripted test prompts only. By default the script drops account and
user ids, `turn_context`/`world_state` rows, `thread_settings_applied` events, developer and system
messages, injected user context (AGENTS.md, environment context), encrypted content, base/developer
instructions, plugin and skill names, and every prompt not passed with `--keep-prompt`. Any path
outside the scratch repo becomes its basename.

`python3 scripts/scrub-rollout.py --check FILE...` reports what is left (key path and reason, never
the value); `tests/test_scrub_rollout.py` runs it on every fixture recorded from a real session.
