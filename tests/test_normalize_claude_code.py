"""Claude Code normalizer, exercised only against real recorded payloads."""
import json

import pytest

from agent_autosave.events import validate
from agent_autosave.normalize.claude_code import normalize
from tests.helpers import load_payload, payload_names

TS = "2026-10-15T00:00:00Z"


def payload(name):
    return load_payload("claude-code", name)


def run(name, tasks=None, turn=1):
    events, tasks = normalize(payload(name), ts=TS, turn=turn, tasks=tasks)
    for item in events:
        validate(item)
    return events, tasks


def test_every_hook_event_has_a_real_fixture():
    names = {load_payload("claude-code", name)["hook_event_name"] for name in payload_names("claude-code")}
    assert names == {"SessionStart", "UserPromptSubmit", "PostToolUse", "PostToolUseFailure",
                     "Stop", "StopFailure", "PreCompact", "SessionEnd"}


def test_session_start_keeps_source_and_transcript():
    [event], _ = run("SessionStart")
    assert event["kind"] == "session_start" and event["source"] == "resume"
    assert event["transcript"].endswith(".jsonl")


def test_prompt_and_turn_end():
    [prompt], _ = run("UserPromptSubmit")
    assert prompt["kind"] == "prompt" and prompt["text"].startswith("Reply OK.")
    [stop], _ = run("Stop")
    assert stop["kind"] == "turn_end" and stop["last_message"].startswith("I added two steps")


def test_successful_bash_records_command_not_output():
    [event], _ = run("PostToolUse-Bash")
    assert event["summary"] == "python3 hello.py" and event["exit"] == 0
    # The output rides along only so a test runner's failure summary can be
    # read (shellexit); what is stored never has a passing command's output.
    from agent_autosave.config import resolve
    from agent_autosave.events import prepare
    stored = prepare(event, resolve())
    assert "output_tail" not in stored and "hello autosave" not in json.dumps(stored)


def test_failed_bash_exit_code_from_error_text():
    [event], _ = run("PostToolUseFailure")
    assert event["exit"] == 7 and event["output_tail"] == "Exit code 7"


@pytest.mark.parametrize("name,verb", [("PostToolUse-Edit", "Edited "), ("PostToolUse-Write", "Wrote ")])
def test_edit_and_write_record_relative_files_without_contents(name, verb):
    [event], _ = run(name)
    assert event["summary"].startswith(verb) and event["files"] == ["hello.py"]
    raw = payload(name)
    for secret in (raw["tool_input"].get("content"), raw["tool_input"].get("new_string")):
        if secret:
            assert secret not in json.dumps(event)


def test_todowrite_replaces_whole_plan():
    [event], _ = run("PostToolUse-TodoWrite")
    assert event["kind"] == "plan"
    assert event["items"] == [{"step": "Implement hello.py", "state": "doing"},
                              {"step": "Validate output", "state": "todo"}]


def test_task_tools_build_incremental_plan():
    _, tasks = run("PostToolUse-TaskCreate-1")
    events, tasks = run("PostToolUse-TaskCreate-2", tasks)
    assert [i["state"] for i in events[0]["items"]] == ["todo", "todo"]
    events, tasks = run("PostToolUse-TaskUpdate", tasks)
    assert events[0]["items"][0]["state"] == "doing"


def test_task_update_without_create_raises():
    with pytest.raises(ValueError):
        run("PostToolUse-TaskUpdate")


def test_stop_failure_maps_real_error_to_reason():
    [event], _ = run("StopFailure")
    assert event == dict(event, kind="interrupted", reason="auth")


@pytest.mark.parametrize("error,reason", [("rate_limit", "rate_limit"), ("overloaded", "overloaded"),
                                          ("server_error", "server_error"), ("something_new", "unknown")])
def test_stop_failure_reason_table(error, reason):
    # Mapping table only; the payload shape itself is the real recorded one.
    raw = dict(payload("StopFailure"), error=error)
    [event], _ = normalize(raw, ts=TS, turn=1)
    assert event["reason"] == reason


def test_compact_and_session_end():
    [compact], _ = run("PreCompact")
    assert compact["trigger"] == "manual"
    [end], _ = run("SessionEnd")
    assert end["kind"] == "session_end" and end["reason"] == "other"


@pytest.mark.parametrize("bad", [{}, {"hook_event_name": "Unknown", "session_id": "s"},
                                 {"hook_event_name": "Stop"}])
def test_unknown_or_incomplete_payloads_raise(bad):
    with pytest.raises((ValueError, KeyError)):
        normalize(bad, ts=TS, turn=0)
