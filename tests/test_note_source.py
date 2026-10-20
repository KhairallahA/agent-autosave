"""Who wrote a note: a person at any terminal is "you"; an agent only when
its own marker for its own tool calls is in the environment."""
import pytest

from tests.memory_helpers import finish_task, repo  # noqa: F401

# What each terminal looks like. The editor ones are copied from a Cursor and
# a VS Code integrated terminal with Claude Code's extension installed.
PLAIN = {"TERM_PROGRAM": "Apple_Terminal", "SHELL": "/bin/zsh"}
CURSOR_TERMINAL = {"TERM_PROGRAM": "vscode", "TERM_PROGRAM_VERSION": "1.99.3", "CURSOR_TRACE_ID": "a1b2c3",
                   "VSCODE_GIT_IPC_HANDLE": "/tmp/vscode-git-1.sock", "VSCODE_INJECTION": "1",
                   "CURSOR_CLI": "1", "CLAUDECODE": "1"}
CLAUDE_TOOL = {"CLAUDECODE": "1", "CLAUDE_CODE_CHILD_SESSION": "1", "CLAUDE_CODE_SESSION_ID": "s1"}
CODEX_TOOL = {"CODEX_THREAD_ID": "019a-thread", "CODEX_TOOL_CALL_ID": "call_abc123"}
CURSOR_AGENT_TOOL = dict(CURSOR_TERMINAL, CURSOR_AGENT="1")
CASES = [(PLAIN, "you"), (CURSOR_TERMINAL, "you"), (CLAUDE_TOOL, "claude-code"), (CODEX_TOOL, "codex"),
         (CURSOR_AGENT_TOOL, "cursor"),
         ({"GEMINI_CLI": "1"}, "you"),  # also set on commands a person types after "!" in Gemini CLI
         ({"CODEX_THREAD_ID": "019a-thread"}, "you"),  # a thread id alone is not a tool call
         ({"CLAUDE_CODE_CHILD_SESSION": ""}, "you")]
IDS = ["plain", "cursor-terminal", "claude-tool", "codex-tool", "cursor-agent", "gemini", "codex-thread", "empty"]


def _env(monkeypatch, values):
    for name, value in values.items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize("env, expected", CASES, ids=IDS)
def test_the_source_comes_from_the_environment_only(env, expected):
    from agent_autosave.origin import note_source
    assert note_source(env) == expected


@pytest.mark.parametrize("env, expected", CASES[:5], ids=IDS[:5])
def test_note_keep_credits_the_shell_it_ran_in(repo, monkeypatch, env, expected):
    repo.prompt("Speed up the importer")  # a Claude Code session is open the whole time
    _env(monkeypatch, env)
    assert repo.cli("note", "--keep", "--type", "decision", "Keep greet.py under 10 lines")[0] == 0
    [row] = repo.cli("memory", "review")[1]["data"]["pending"]
    assert row["source"]["agent"] == expected


@pytest.mark.parametrize("env, expected", CASES[:5], ids=IDS[:5])
def test_archived_notes_credit_the_shell_they_ran_in(repo, monkeypatch, env, expected):
    _env(monkeypatch, env)
    finish_task(repo)  # notes taken while the Claude Code session is open
    rows = repo.cli("memory", "review")[1]["data"]["pending"]
    assert [r["source"]["agent"] for r in rows] == [expected, expected]


def test_older_notes_without_a_source_are_yours(repo):
    """Ledgers written before the source was recorded: no open-session guess."""
    repo.prompt("Speed up the importer")
    store = repo.root / ".autosave"
    assert repo.cli("note", "--type", "decision", "Keep greet.py under 10 lines")[0] == 0
    for path in store.rglob("*.jsonl"):
        text = path.read_text()
        if '"kind": "note"' in text or '"kind":"note"' in text:
            path.write_text(text.replace(', "by": "you"', "").replace(',"by":"you"', ""))
    repo.stop("Done.")
    assert repo.cli("done")[0] == 0
    [row] = repo.cli("memory", "review")[1]["data"]["pending"]
    assert row["source"]["agent"] == "you"
