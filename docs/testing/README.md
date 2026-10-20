# Testing

**Who it's for:** Contributors sending a change, and anyone running the manual checks before a release.

Read this before sending a change: what the automated suite proves, how to
run it, and the manual checks only a real agent can do.

## Automated tests

```sh
python -m pytest -m "not slow" -n auto     # the day-to-day loop
scripts/check.sh                           # everything, on Python 3.9 to 3.14 (needs uv)
```

| What | Where | Rebuild after an intended change |
| --- | --- | --- |
| The compiler: each rule against golden saves | `tests/test_compiler.py`, `tests/fixtures/compiler/` | `python scripts/update-goldens.py` |
| Command output, styled and plain | `tests/test_ui.py`, `tests/fixtures/ui/` | `python scripts/update-goldens.py` |
| The wizard, through a real pty with keystrokes | `tests/test_wizard.py` | |
| Every output block in the docs and README matches a real run | `tests/test_docs_output.py` | `python scripts/build-docs.py` |
| Each example replays to its committed files | `tests/test_examples.py`, `examples/*/replay.py` | `python scripts/build-examples.py` |
| Recorded hook payloads from real agents | `tests/test_claude_code_sessions.py`, `tests/test_codex_adapter.py`, `tests/fixtures/` | record new ones (below) |
| Saves and ledgers against the JSON schemas | `tests/test_schemas.py`, `schemas/` | |
| Hooks stay under 300 ms and import nothing from the UI | `tests/test_performance.py`, `tests/test_package.py` | |
| Every relative link resolves | `tests/test_docs.py`, `scripts/check-links.py` | |

Review the diff of anything a rebuild script changes before committing it.

## Manual tests with real agents

The test suite replays recorded hook payloads; it never starts an agent.
These guides check the parts only a real agent can show: that its hooks fire
as recorded, that the save reaches the next agent, and how new payloads are
recorded as fixtures. Each runs in a throwaway repo under /tmp with the agent
logins you already have.

| Guide | What it checks |
| --- | --- |
| [claude-code.md](claude-code.md) | Claude Code hooks, recording fixtures, the handoff into a new session |
| [codex.md](codex.md) | Codex trust, hooks, rollout exit codes, recording fixtures, the handoff |
| [cursor.md](cursor.md) | Cursor capture and the save injected on a conversation's first prompt |
| [hook-agents.md](hook-agents.md) | Gemini CLI, Antigravity, Copilot CLI and OpenCode: record payloads and `resume` |
| [resume.md](resume.md) | `status`, `note`, `show --context`, `resume`, `clear` and `history` across two agents |
| [install.md](install.md) | byte-exact `remove`, `doctor` going red and its fix, the shared user-level registry |
| [release-checklist.md](release-checklist.md) | The checks to run before a release: Homebrew, the wizard, crash takeover, `go`, tasks and slots, memory and recall, the vault, agentic-stack, recording an agent |

## Install from a checkout

```sh
git clone https://github.com/KhairallahA/agent-autosave && cd agent-autosave
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
which agent-autosave          # .../agent-autosave/.venv/bin/agent-autosave
python -m pytest -q           # all tests pass before you start
```

The hooks store the absolute path of this venv's `agent-autosave`, so they
work from any terminal, the desktop app or an IDE.

Codex only installs into a folder it trusts: start `codex` in the scratch
repo and accept the trust prompt (then `/quit`) before `agent-autosave init`.

When a run shows something the tests do not, open an issue with the agent
version and the commands you ran; recorded payloads are welcome in a pull
request (see [CONTRIBUTING.md](../../CONTRIBUTING.md)).

## See also

- [Architecture](../architecture.md)
- [Releasing](../releasing.md): where these checks fit
- `CONTRIBUTING.md`: the rules for a change
