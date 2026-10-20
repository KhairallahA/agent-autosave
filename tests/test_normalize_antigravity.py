"""Antigravity payloads (from antigravity.google/docs/hooks examples;
doc-derived until recorded) -> events."""
from agent_autosave.events import validate
from agent_autosave.normalize.antigravity import normalize

COMMON = {"conversationId": "ec33ebf9-0cba-4100-8142-c61503f6c587", "workspacePaths": ["/workspace/project"],
          "transcriptPath": "~/t.jsonl", "artifactDirectoryPath": "~/a", "modelName": "gemini-3.6-flash-medium"}
TS = "2026-10-19T10:00:01Z"


def run(name, **fields):
    events, _ = normalize(dict(COMMON, hook_event_name=name, **fields), ts=TS, turn=0)
    for event in events:
        validate(event)
    return events


def test_the_documented_failing_command():
    event = run("PostToolUse", toolCall={"name": "run_command", "args": {"CommandLine": "npm test",
                                                                          "Cwd": "/workspace/project"}},
                stepIdx=5, error="exit status 1")[0]
    assert event["agent"] == "antigravity" and event["session"] == COMMON["conversationId"]
    assert event["summary"] == "npm test" and event["exit"] == 1


def test_edits_session_start_and_stop():
    edit = run("PostToolUse", toolCall={"name": "write_to_file", "args": {"TargetFile": "/workspace/project/a.py"}},
               error="")[0]
    assert edit["files"] == ["a.py"]
    assert run("SessionStart")[0]["kind"] == "session_start"
    assert run("Stop", executionNum=1, terminationReason="model_stop", fullyIdle=True)[-1]["kind"] == "turn_end"
