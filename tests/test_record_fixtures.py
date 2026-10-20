"""Recorder unit tests use synthetic stdin, never label it as a vendor fixture."""
import json
import os
from pathlib import Path
import subprocess
import sys

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/record-fixtures.py"


def test_setup_is_scratch_only_and_refuses_existing_directory(tmp_path):
    scratch, output = tmp_path / "scratch", tmp_path / "recordings"
    command = [sys.executable, str(SCRIPT), "--setup", str(scratch), "--output", str(output), "--version", "test"]
    result = subprocess.run(command, capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr
    settings = scratch / ".claude/settings.json"
    original = settings.read_bytes()
    hooks = json.loads(original)["hooks"]
    assert set(hooks) == {"SessionStart","UserPromptSubmit","PostToolUse","PostToolUseFailure","Stop","StopFailure","PreCompact","SessionEnd"}
    assert all(row["hooks"][0]["args"][0] == str(SCRIPT) for entries in hooks.values() for row in entries)
    assert subprocess.run(command, capture_output=True, timeout=5).returncode != 0
    assert settings.read_bytes() == original


def test_recorder_opt_in_redacts_before_write_and_stays_silent(tmp_path):
    output = tmp_path / "recordings"
    command = [sys.executable, str(SCRIPT), "--output", str(output), "--version", "test"]
    env = dict(os.environ, AGENT_AUTOSAVE_RECORD="0")
    payload = json.dumps({"hook_event_name":"UserPromptSubmit", "prompt":"sk-proj-abcdefgh123456789", "cwd":str(tmp_path)})
    result = subprocess.run(command, input=payload, text=True, capture_output=True, cwd=tmp_path, env=env, timeout=5)
    assert result.returncode == 0 and not output.exists()
    env["AGENT_AUTOSAVE_RECORD"] = "1"
    result = subprocess.run(command, input=payload, text=True, capture_output=True, cwd=tmp_path, env=env, timeout=5)
    assert result.returncode == 0 and result.stdout == result.stderr == ""
    records = list(output.glob("UserPromptSubmit-*.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text())
    assert record["payload"]["prompt"] == "[redacted:api_key]"
    assert record["claude_version"] == "test"


def test_invalid_stdin_fails_open_with_diagnostic(tmp_path):
    result = subprocess.run([sys.executable, str(SCRIPT), "--output", str(tmp_path/"recordings")],
                            input=b"invalid json", capture_output=True, cwd=tmp_path,
                            env=dict(os.environ, AGENT_AUTOSAVE_RECORD="1"), timeout=5)
    assert result.returncode == 0 and result.stdout == result.stderr == b""
    assert "fixture.record" in (tmp_path/".autosave/errors.log").read_text()
