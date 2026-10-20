"""Hook entrypoint: real payloads end to end, fuzzing, recording, budgets."""
import json
import os
from pathlib import Path
import subprocess
import time

import pytest

from agent_autosave.installer.install import install
from tests.helpers import LENIENT_CLI, load_payload



def payload(name, project, session="S1"):
    data = load_payload("claude-code", name)
    if isinstance(data.get("tool_input"), dict) and "file_path" in data["tool_input"]:
        data["tool_input"]["file_path"] = str(Path(project) / Path(data["tool_input"]["file_path"]).name)
    data["cwd"], data["session_id"] = str(project), session
    return data


def hook(project, event, data, env=None):
    raw = data if isinstance(data, bytes) else json.dumps(data).encode()
    environment = dict(os.environ if env is None else env)
    environment.pop("AGENT_AUTOSAVE_RECORD", None) if env is None else None
    start = time.perf_counter()
    result = subprocess.run([*LENIENT_CLI, "hook", "claude-code", event],
                            input=raw, capture_output=True, cwd=str(project), env=environment, timeout=10)
    return result, (time.perf_counter() - start) * 1000


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    install(root)
    return root


SESSION = [("UserPromptSubmit", "UserPromptSubmit"), ("PostToolUse", "PostToolUse-TodoWrite"),
           ("PostToolUse", "PostToolUse-Write"), ("PostToolUse", "PostToolUse-Bash"),
           ("PostToolUseFailure", "PostToolUseFailure"), ("StopFailure", "StopFailure")]


def replay(project):
    for event, name in SESSION:
        result, _ = hook(project, event, payload(name, project))
        assert result.returncode == 0 and result.stdout == b"" and result.stderr == b""


def test_session_builds_interrupted_save(project):
    replay(project)
    save = json.loads((project / ".autosave/save.json").read_text())
    assert save["status"] == "interrupted" and save["interrupted"]["reason"] == "auth"
    assert save["goal"].startswith("Reply OK.")
    assert [p["state"] for p in save["plan"]] == ["doing", "todo"]
    assert save["failing"]["exit"] == 7
    assert "hello.py" in save["files_touched"]
    assert not (project / ".autosave/errors.log").exists()


def test_new_session_receives_the_save(project):
    replay(project)
    result, _ = hook(project, "SessionStart", payload("SessionStart", project, session="S2"))
    assert result.returncode == 0
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["hookEventName"] == "SessionStart"
    text = output["additionalContext"]
    assert "unfinished task from Claude Code" in text and "authentication error" in text
    assert "SystemExit(7)" in text and len(text) <= 4000
    # The new session's own start does not clear the interrupted status.
    assert json.loads((project / ".autosave/save.json").read_text())["status"] == "interrupted"


def test_session_end_pauses_and_next_start_injects(project):
    for event, name in SESSION[:-1] + [("SessionEnd", "SessionEnd")]:
        hook(project, event, payload(name, project))
    result, _ = hook(project, "SessionStart", payload("SessionStart", project, session="S2"))
    assert "session ended" in json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]


def test_resuming_the_session_that_wrote_an_active_save_injects_nothing(project):
    hook(project, "UserPromptSubmit", payload("UserPromptSubmit", project))
    result, _ = hook(project, "SessionStart", payload("SessionStart", project, session="S1"))
    assert result.stdout == b""
    result, _ = hook(project, "SessionStart", payload("SessionStart", project, session="S2"))
    assert b"may still be running" in result.stdout


def test_turns_count_prompts_per_session(project):
    for _ in range(3):
        hook(project, "UserPromptSubmit", payload("UserPromptSubmit", project))
    hook(project, "Stop", payload("Stop", project))
    rows = [json.loads(l) for l in (project / ".autosave/events.jsonl").read_text().splitlines()]
    assert [r["turn"] for r in rows] == [1, 2, 3, 3]


def test_uninitialized_directory_is_silent_and_writes_nothing(tmp_path):
    result, _ = hook(tmp_path, "UserPromptSubmit", payload("UserPromptSubmit", tmp_path))
    assert result.returncode == 0 and result.stdout == b""
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("raw", [b"", b"{", b"\xff\x00\xfe", b"[]", b"null", b'{"hook_event_name":"Stop"}',
                                 b'{"hook_event_name":"Stop","session_id":"s"}',
                                 b"x" * (10 * 1024 * 1024)],
                         ids=["empty", "truncated", "binary", "array", "null", "no-session", "no-cwd", "10mb"])
def test_fuzzed_payloads_exit_zero_quickly_and_silently(project, raw):
    result, elapsed = hook(project, "Stop", raw)
    assert result.returncode == 0 and result.stdout == b"" and result.stderr == b""
    # Wall time is asserted in tests/test_performance.py (serial timing phase).


def test_errors_are_logged_not_raised(project):
    hook(project, "Stop", b'{"hook_event_name":"Stop","session_id":"s","cwd":' + json.dumps(str(project)).encode() + b"}")
    hook(project, "PostToolUse", payload("PostToolUse-TaskUpdate", project))
    log = (project / ".autosave/errors.log").read_text()
    assert "hook.claude-code" in log


def test_secrets_in_payloads_never_reach_disk(project):
    data = payload("UserPromptSubmit", project)
    data["prompt"] = "Please deploy with key sk-proj-abcdefghijklmnop1234567890 and ghp_abcdefghijklmnopqrstuvwxyz0123456789"
    env = dict(os.environ, AGENT_AUTOSAVE_RECORD="1")
    hook(project, "UserPromptSubmit", data, env=env)
    for path in (project / ".autosave").rglob("*"):
        if path.is_file():
            body = path.read_text(errors="replace")
            assert "sk-proj-abcdefghijklmnop" not in body and "ghp_abcdefghijklmnop" not in body


def test_record_mode_writes_redacted_envelope(project, tmp_path):
    out = tmp_path / "rec"
    env = dict(os.environ, AGENT_AUTOSAVE_RECORD="1", AGENT_AUTOSAVE_RECORD_DIR=str(out),
               AGENT_AUTOSAVE_RECORD_VERSION="2.1.test")
    data = payload("Stop", project)
    data["transcript_path"] = str(Path.home() / ".claude/x.jsonl")
    result, _ = hook(project, "Stop", data, env=env)
    assert result.returncode == 0 and result.stdout == b""
    [path] = list((out / "claude-code").glob("Stop-*.json"))
    record = json.loads(path.read_text())
    assert record["agent_version"] == "2.1.test" and record["payload"]["hook_event_name"] == "Stop"
    assert str(Path.home()) not in path.read_text()
    # Record mode is additive: the event is still captured.
    assert "turn_end" in (project / ".autosave/events.jsonl").read_text()


def test_record_mode_default_dir_and_off_by_default(project):
    hook(project, "Stop", payload("Stop", project))
    assert not (project / ".autosave/recordings").exists()
    hook(project, "Stop", payload("Stop", project), env=dict(os.environ, AGENT_AUTOSAVE_RECORD="1"))
    assert len(list((project / ".autosave/recordings/claude-code").glob("Stop-*.json"))) == 1


def test_record_mode_still_records_invalid_shapes(project, tmp_path):
    env = dict(os.environ, AGENT_AUTOSAVE_RECORD="1", AGENT_AUTOSAVE_RECORD_DIR=str(tmp_path / "r"))
    data = {"hook_event_name": "FutureEvent", "session_id": "s", "cwd": str(project)}
    result, _ = hook(project, "FutureEvent", data, env=env)
    assert result.returncode == 0
    assert list((tmp_path / "r/claude-code").glob("FutureEvent-*.json"))


