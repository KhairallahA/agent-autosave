"""The real Antigravity recordings (antigravity-cli 1.3.5 and 1.4.2, one session each:
SessionStart, write hello.py, run `python3 -c "raise SystemExit(3)"`, Stop)
run through the normalizer.

The recorded PostToolUse carries no exit code: `error` is "" even for the
command that exited 3, and there is no output. So a command's exit is
unknown (null), never 0. No payload carries the prompt or the agent's last
message."""
import json
from pathlib import Path

import pytest

from agent_autosave.events import validate
from agent_autosave.normalize.antigravity import normalize
from tests.helpers import ROOT

RECORDED = ROOT / "tests/fixtures/antigravity/recorded"
FILES = sorted(RECORDED.glob("*.json"))
TS = "2026-10-19T18:32:00Z"
KNOWN = {"artifactDirectoryPath", "conversationId", "error", "executionNum", "fullyIdle", "modelName", "stepIdx",
         "terminationReason", "toolCall", "transcriptPath", "workspacePaths"}


def load(path):
    envelope = json.loads(path.read_text())
    return envelope, dict(envelope["payload"], hook_event_name=path.name.split("-")[0])


def run(path):
    events, _ = normalize(load(path)[1], ts=TS, turn=0)
    for event in events:
        validate(event)
    return events


def test_there_are_two_recorded_sessions_of_four_hooks():
    names = sorted(p.name.split("-")[0] for p in FILES)
    assert names == ["PostToolUse"] * 4 + ["SessionStart"] * 2 + ["Stop"] * 2
    assert {load(p)[0]["agent_version"] for p in FILES} == {"1.3.5", "1.4.2"}
    assert len({load(p)[1]["conversationId"] for p in FILES}) == 2


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_every_recording_normalizes_to_valid_events(path):
    events = run(path)
    assert events and all(e["agent"] == "antigravity" and e["session"] == load(path)[1]["conversationId"]
                          for e in events)


def commands():
    return [p for p in FILES if p.name.startswith("PostToolUse") and load(p)[1]["toolCall"]["name"] == "run_command"]


def test_raise_system_exit_3_is_never_recorded_as_a_pass():
    found = commands()
    assert len(found) == 2
    for path in found:
        payload = load(path)[1]
        assert payload["toolCall"]["args"]["CommandLine"] == 'python3 -c "raise SystemExit(3)"'
        assert payload["error"] == ""  # what Antigravity sends for exit 3: no exit code at all
        [event] = run(path)
        assert event["tool"] == "Bash" and event["summary"] == payload["toolCall"]["args"]["CommandLine"]
        assert event["exit"] is None  # unknown, never 0


def test_the_recorded_write_names_the_file_relative_to_the_workspace():
    writes = [p for p in FILES if p.name.startswith("PostToolUse") and p not in commands()]
    assert len(writes) == 2
    for path in writes:
        [event] = run(path)
        assert event["files"] == ["hello.py"] and event["tool"] == "Write"


def test_session_start_and_stop():
    for path in FILES:
        kinds = [e["kind"] for e in run(path)]
        if path.name.startswith("SessionStart"):
            assert kinds == ["session_start"]
        elif path.name.startswith("Stop"):
            assert kinds[-1] == "turn_end"


def test_no_payload_carries_a_prompt_or_a_last_message():
    """If a later Antigravity adds a field, this fails and the adapter gets looked at again."""
    for path in FILES:
        assert set(load(path)[1]) - {"hook_event_name"} <= KNOWN, path.name
    for path in FILES:
        if path.name.startswith("Stop"):
            assert not any(e.get("text") for e in run(path))


def test_a_documented_error_string_still_gives_its_exit():
    payload = dict(load(commands()[0])[1], error="exit status 2")
    [event] = normalize(payload, ts=TS, turn=0)[0]
    assert event["exit"] == 2
    payload["error"] = "command timed out"
    assert normalize(payload, ts=TS, turn=0)[0][0]["exit"] == 1


def test_the_readme_marks_antigravity_recorded_with_its_version():
    row = next(line for line in (ROOT / "README.md").read_text().splitlines() if line.startswith("| [Antigravity]"))
    assert "**Recorded**: antigravity-cli 1.3.5 and 1.4.2" in row
    page = (ROOT / "docs/adapters/antigravity.md").read_text()
    assert "not recorded yet" not in page and "1.3.5 and 1.4.2" in page
