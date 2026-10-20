"""Gemini CLI payloads (shapes from geminicli.com/docs/hooks/reference/;
doc-derived until recorded) -> events."""
import pytest

from agent_autosave.events import validate
from agent_autosave.normalize.gemini import normalize

BASE = {"session_id": "g1", "transcript_path": "/t.json", "cwd": "/repo", "timestamp": "2026-10-19T10:00:00Z"}
TS = "2026-10-19T10:00:01Z"


def run(name, **fields):
    events, _ = normalize(dict(BASE, hook_event_name=name, **fields), ts=TS, turn=1)
    for event in events:
        validate(event)
    return events


def test_lifecycle_events():
    assert run("SessionStart", source="resume")[0]["source"] == "resume"
    assert run("BeforeAgent", prompt="Fix the bug")[0] == {"v": 1, "ts": TS, "agent": "gemini", "session": "g1",
                                                         "turn": 1, "kind": "prompt", "text": "Fix the bug"}
    assert run("AfterAgent", prompt="x", prompt_response="Done.")[-1]["kind"] == "turn_end"
    assert run("PreCompress", trigger="manual")[0] == dict(run("PreCompress", trigger="manual")[0], kind="compact",
                                                           trigger="manual")
    assert run("SessionEnd", reason="exit")[0]["reason"] == "exit"


def test_shell_edit_and_failure():
    shell = run("AfterTool", tool_name="run_shell_command", tool_input={"command": "pytest -q"},
                tool_response={"llmContent": "ok", "returnDisplay": "ok"})[0]
    assert shell["summary"] == "pytest -q" and shell["exit"] == 0
    failed = run("AfterTool", tool_name="run_shell_command", tool_input={"command": "pytest -q"},
                 tool_response={"llmContent": "", "error": "Exit code 1"})[0]
    assert failed["exit"] == 1 and failed["output_tail"] == "Exit code 1"
    edit = run("AfterTool", tool_name="replace", tool_input={"file_path": "/repo/src/app.py"},
               tool_response={"llmContent": "ok"})[0]
    assert edit["files"] == ["src/app.py"] and edit["summary"] == "Edited src/app.py"
    other = run("AfterTool", tool_name="mcp_docs_search", tool_input={"q": "x"}, tool_response={})[0]
    assert other["summary"] == "mcp_docs_search"


def test_unknown_event_is_rejected():
    with pytest.raises(ValueError):
        run("BeforeModel")
