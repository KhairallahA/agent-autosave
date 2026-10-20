"""Shared set-up for the memory tests: a repo with one finished task."""
import json
import os

import pytest

from tests.scenario import Repo


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_AUTOSAVE_SESSION_END", "sync")
    monkeypatch.setenv("NO_COLOR", "1")
    return Repo(tmp_path / "repo", agents=("claude-code", "codex"))


def finish_task(repo, goal="Add a CSV export for the billing rows", session="s1", agent="claude-code",
                decision="Stream rows with a generator, not pandas, because billing tables are huge",
                tried="Loading the whole billing table with pandas ran out of memory"):
    """One task with a decision and a dead end, finished with `done`."""
    repo.prompt(goal, session=session, agent=agent) if agent == "claude-code" else codex_prompt(repo, goal, session)
    repo.bash("pytest -q", session=session)
    if decision:
        repo.cli("note", "--type", "decision", decision)
    if tried:
        repo.cli("note", "--type", "tried", tried)
    repo.stop("Done: the export streams rows.", session=session)
    assert repo.cli("done")[0] == 0
    return repo.save()


def codex_prompt(repo, text, session):
    from tests.helpers import load_payload
    payload = load_payload("codex", "UserPromptSubmit")
    payload.update(prompt=text, session_id=session)
    return repo.send("codex", payload)


def accept_all(repo):
    code, out = repo.cli("memory", "review")
    ids = [row["id"] for row in out["data"]["pending"]]
    for candidate in ids:
        assert repo.cli("memory", "accept", candidate)[0] == 0
    return ids


def set_config(repo, **sections):
    path = repo.root / ".autosave/config.json"
    value = json.loads(path.read_text())
    for name, fields in sections.items():
        value[name] = dict(value.get(name) or {}, **fields)
    path.write_text(json.dumps(value))


def context_of(output):
    """additionalContext from a hook's stdout ('' when it printed nothing)."""
    if not output:
        return ""
    data = json.loads(output)
    return (data.get("hookSpecificOutput") or {}).get("additionalContext") or data.get("additional_context") or ""


__all__ = ["repo", "finish_task", "accept_all", "set_config", "context_of", "os"]
