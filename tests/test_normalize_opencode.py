"""OpenCode plugin payloads (built by adapters/opencode/agent-autosave.js
from the events in opencode.ai/docs/plugins/) -> events."""
from agent_autosave.events import validate
from agent_autosave.normalize.opencode import normalize

TS = "2026-10-19T10:00:01Z"


def run(name, **fields):
    events, _ = normalize(dict({"session_id": "o1", "cwd": "/repo", "hook_event_name": name}, **fields),
                          ts=TS, turn=0)
    for event in events:
        validate(event)
    return events


def test_session_and_tools():
    assert run("session.created")[0]["kind"] == "session_start"
    bash = run("tool.execute.after", tool="bash", args={"command": "bun test"})[0]
    assert bash["summary"] == "bun test" and bash["agent"] == "opencode"
    edit = run("tool.execute.after", tool="edit", args={"filePath": "/repo/src/a.ts"})[0]
    assert edit["files"] == ["src/a.ts"]
    assert run("session.idle")[-1]["kind"] == "turn_end"
    assert run("session.compacted")[0]["kind"] == "compact"
