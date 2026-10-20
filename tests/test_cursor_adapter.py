"""Cursor hooks end to end on doc-derived payloads (tests/fixtures/cursor/README.md)."""
import json
import subprocess

import pytest

from agent_autosave.installer.install import install
from tests.helpers import LENIENT_CLI, CMD, load_payload


def cursor_payload(name, project, conversation="c-1"):
    data = load_payload("cursor", name)
    data["workspace_roots"] = [str(project)]
    data["conversation_id"] = conversation
    if "session_id" in data:
        data["session_id"] = conversation
    if "file_path" in data:
        data["file_path"] = str(project / "src/app.py")
    return data


def run_hook(project, agent, event, data):
    return subprocess.run([*LENIENT_CLI, "hook", agent, event],
                          input=json.dumps(data).encode(), capture_output=True, cwd=str(project), timeout=10)


def events(project):
    path = project / ".autosave/events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.fixture
def cursor_project(tmp_path, home):
    project = tmp_path / "repo"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    install(project, ("cursor", "claude-code"), command=CMD)
    return project


def test_cursor_session_builds_a_failing_save(cursor_project):
    p = cursor_project
    for name in ("sessionStart", "beforeSubmitPrompt", "postToolUse-Read", "afterFileEdit", "postToolUse-Shell-fail",
                 "stop"):
        data = cursor_payload(name, p)
        result = run_hook(p, "cursor", data["hook_event_name"], data)
        assert result.returncode == 0 and not result.stderr
    kinds = [e["kind"] for e in events(p)]
    assert kinds == ["session_start", "prompt", "tool", "tool", "turn_end"]
    shell = [e for e in events(p) if e.get("tool") == "Shell"][0]
    assert shell["exit"] == 1 and shell["summary"] == "npm test" and shell["output_tail"] == "1 failing"
    # npm test is a test runner: only its failure summary is kept, not the stack trace.
    edit = [e for e in events(p) if e.get("tool") == "Write"][0]
    assert edit["files"] == ["src/app.py"]
    save = json.loads((p / ".autosave/save.json").read_text())
    assert save["goal"] == "Make the build pass on every platform"


def test_cursor_injects_on_the_first_prompt_of_each_conversation_only(cursor_project):
    p = cursor_project
    for name in ("beforeSubmitPrompt", "postToolUse-Shell-fail"):
        data = cursor_payload(name, p, "old")
        run_hook(p, "cursor", data["hook_event_name"], data)
    end = cursor_payload("sessionEnd", p, "old")
    run_hook(p, "cursor", "sessionEnd", end)
    first = run_hook(p, "cursor", "beforeSubmitPrompt", dict(cursor_payload("beforeSubmitPrompt", p, "new"), prompt="go on"))
    out = json.loads(first.stdout)
    assert out["continue"] is True and "[agent-autosave resume context]" in out["additional_context"]
    assert "npm test" in out["additional_context"]
    second = run_hook(p, "cursor", "beforeSubmitPrompt", dict(cursor_payload("beforeSubmitPrompt", p, "new"), prompt="more"))
    assert json.loads(second.stdout) == {"continue": True}


def test_cursor_prompt_hook_always_continues_even_on_bad_input(cursor_project):
    result = subprocess.run([*LENIENT_CLI, "hook", "cursor", "beforeSubmitPrompt"],
                            input=b"not json", capture_output=True, cwd=str(cursor_project), timeout=10)
    assert result.returncode == 0 and json.loads(result.stdout) == {"continue": True}


def test_cursor_stop_error_is_an_unknown_interruption(cursor_project):
    p = cursor_project
    data = cursor_payload("beforeSubmitPrompt", p)
    run_hook(p, "cursor", "beforeSubmitPrompt", data)
    run_hook(p, "cursor", "stop", cursor_payload("stop-error", p))
    assert events(p)[-1]["kind"] == "interrupted" and events(p)[-1]["reason"] == "unknown"


def test_claude_hooks_run_by_cursor_are_dropped(cursor_project):
    p = cursor_project
    claude_like = {"session_id": "c-1", "hook_event_name": "UserPromptSubmit", "prompt": "twice?", "cwd": str(p),
                   "cursor_version": "2.4.21", "conversation_id": "c-1"}
    result = run_hook(p, "claude-code", "UserPromptSubmit", claude_like)
    assert result.returncode == 0 and result.stdout == b"" and events(p) == []


def test_init_warns_that_cursor_runs_claude_hooks(tmp_path, home, monkeypatch, capsys):
    from agent_autosave import cli
    from agent_autosave.installer import detect
    for name in ("claude_code", "codex", "cursor", "gemini", "antigravity"):
        monkeypatch.setattr(detect, name, (lambda n: lambda **k: {"installed": n in ("claude_code", "cursor"),
                                                                   "version": None, "path": None})(name))
    project = tmp_path / "repo"
    project.mkdir()
    monkeypatch.chdir(project)
    assert cli.main(["--json", "init"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["data"]["agents"] == ["claude-code", "cursor"]
    assert "cursor_runs_claude_hooks" in [w["code"] for w in out["data"]["warnings"]]
    assert (project / ".cursor/rules/agent-autosave.mdc").read_text().startswith("---\n")
