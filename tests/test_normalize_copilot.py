"""Copilot CLI payloads in the VS Code compatible format (from
docs.github.com/en/copilot/reference/hooks-reference; doc-derived until
recorded) -> events."""
from agent_autosave.events import validate
from agent_autosave.normalize.copilot import normalize

BASE = {"session_id": "c1", "timestamp": "2026-10-19T10:00:00Z", "cwd": "/repo"}
TS = "2026-10-19T10:00:01Z"


def run(name, **fields):
    events, _ = normalize(dict(BASE, hook_event_name=name, **fields), ts=TS, turn=2)
    for event in events:
        validate(event)
    return events


def test_prompt_tools_and_end():
    assert run("SessionStart", source="new")[0]["source"] == "startup"
    assert run("UserPromptSubmit", prompt="Add caching")[0]["text"] == "Add caching"
    ok = run("PostToolUse", tool_name="bash", tool_input={"command": "make test"},
             tool_result={"result_type": "success", "text_result_for_llm": "ok"})[0]
    assert ok["summary"] == "make test" and ok["exit"] == 0 and ok["agent"] == "copilot"
    bad = run("PostToolUseFailure", tool_name="bash", tool_input={"command": "make test"}, error="Exit code 2")[0]
    assert bad["exit"] == 2
    edit = run("PostToolUse", tool_name="edit", tool_input={"path": "/repo/x.py"}, tool_result={})[0]
    assert edit["files"] == ["x.py"]
    assert run("Stop", stop_reason="end_turn", stop_hook_active=False)[-1]["kind"] == "turn_end"
    assert run("PreCompact", trigger="auto", transcript_path="/t", custom_instructions="")[0]["kind"] == "compact"
    assert run("SessionEnd", reason="user_exit")[0]["reason"] == "user_exit"
