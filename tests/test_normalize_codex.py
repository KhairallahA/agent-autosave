"""Codex normalizer and rollout reader, against DOC-DERIVED fixtures.

tests/fixtures/codex/ holds hand-written payloads (see its README) until real
recordings replace them; these tests must keep passing on the real files.
"""
import json
from pathlib import Path
import shutil

import pytest

from agent_autosave.events import validate
from agent_autosave.normalize.codex import normalize, patch_files
from agent_autosave.transcripts import codex as rollout
from tests.helpers import load_envelope, load_payload, payload_names, payload_path

FIXTURES = Path(__file__).resolve().parent / "fixtures/codex"
ROLLOUTS = FIXTURES / "rollouts"
TS = "2026-10-15T00:00:00Z"


def payload(name):
    return load_payload("codex", name)


def run(name, tasks=None, facts=None, data=None):
    events, tasks = normalize(data or payload(name), ts=TS, turn=1, tasks=tasks, facts=facts)
    for item in events:
        validate(item)
    return events, tasks


def test_fixtures_are_marked_doc_derived_and_cover_every_wired_hook():
    names = set()
    for name in payload_names("codex"):
        envelope = load_envelope("codex", name)
        names.add(envelope["payload"]["hook_event_name"])
        if envelope.get("doc_derived"):
            assert "DOC-DERIVED" in envelope["provenance"]
    hooks = json.loads((Path(__file__).resolve().parents[1] / "adapters/codex/hooks.json").read_text())["hooks"]
    assert names == set(hooks)


SOURCES = [(name, name.split("-", 1)[1]) for name in payload_names("codex") if name.startswith("SessionStart-")]


@pytest.mark.parametrize("name,source", SOURCES)
def test_session_start_sources(name, source):
    source = "resume" if source == "fork" else source
    [event], _ = run(name)
    assert event["agent"] == "codex" and event["source"] == source
    assert "/.codex/sessions/" in event["transcript"] and isinstance(event["model"], str)


def test_prompt_compact_session_end():
    assert run("UserPromptSubmit")[0][0]["text"].startswith("Use your update_plan tool")
    assert run("PreCompact")[0][0] == dict(run("PreCompact")[0][0], kind="compact", trigger="manual")
    [end], _ = run("SessionEnd")
    assert end["kind"] == "session_end" and end["reason"] == "other"


def test_bash_exit_comes_from_facts_and_success_output_is_never_kept():
    [ok], _ = run("PostToolUse-Bash", facts={"exit": 0})
    from agent_autosave.config import resolve
    from agent_autosave.events import prepare
    assert ok["summary"] == "python3 hello.py" and ok["exit"] == 0 and "output_tail" not in prepare(ok, resolve())
    [fail], _ = run("PostToolUse-Bash-fail", facts={"exit": 7})
    assert fail["exit"] == 7 and fail["summary"] == 'python3 -c "raise SystemExit(7)"'
    [unknown], _ = run("PostToolUse-Bash")  # exit not found in the rollout: unknown
    assert unknown["exit"] is None and "output_tail" not in prepare(unknown, resolve())
    [unknown], _ = run("PostToolUse-Bash-fail", facts={"exit": None})
    assert unknown["exit"] is None and "output_tail" not in prepare(unknown, resolve())


def test_apply_patch_records_files_not_patch_text():
    [event], _ = run("PostToolUse-apply_patch")
    assert event["summary"] == "Edited hello.py" and event["files"] == ["hello.py"]
    assert "print(" not in json.dumps(event)
    patch = ("*** Begin Patch\n*** Update File: /tmp/aa-codex/src/a.py\n*** Move to: src/b.py\n@@\n-x\n+y\n"
             "*** Delete File: old.txt\n*** End Patch\n")
    assert patch_files(patch, "/tmp/aa-codex") == ["src/a.py", "src/b.py", "old.txt"]


def test_update_plan_maps_states_and_suppresses_checklist():
    [plan], tasks = run("PostToolUse-update_plan")
    assert [(i["step"], i["state"]) for i in plan["items"]] == [
        ("Write hello.py", "done"), ("Run it", "doing"), ("Add a failing check", "todo")]
    data = dict(payload("Stop"), last_assistant_message="- [ ] one\n- [x] two")
    assert [e["kind"] for e in run("Stop", tasks=tasks, data=data)[0]] == ["turn_end"]
    assert [e["kind"] for e in run("Stop", data=data)[0]] == ["plan", "turn_end"]


def test_limit_is_reported_once_per_turn_after_turn_end():
    facts = {"limit": "rate_limit", "limit_turn": "turn-2"}
    events, tasks = run("Stop", facts=facts)
    assert [e["kind"] for e in events] == ["turn_end", "interrupted"] and events[1]["reason"] == "rate_limit"
    events, _ = run("SessionEnd", tasks=tasks, facts=facts)
    assert [e["kind"] for e in events] == ["session_end"]
    events, _ = run("SessionEnd", facts=facts)  # Stop never ran for the limited turn
    assert [e["kind"] for e in events] == ["interrupted", "session_end"]


def test_unknown_events_and_sources_are_rejected():
    with pytest.raises(ValueError):
        run("x", data=dict(payload("Stop"), hook_event_name="PermissionRequest"))
    with pytest.raises(ValueError):
        run("x", data=dict(payload("SessionStart-startup"), source="weird"))


def test_rollout_exit_codes_and_limits():
    rows = rollout.tail_lines(ROLLOUTS / "rollout-2026-10-15T10-00-00-normal.jsonl")
    assert rollout.exit_code(rows, "call_ok") == 0 and rollout.exit_code(rows, "call_fail") == 7
    assert rollout.exit_code(rows, "call_missing") is None
    assert rollout.limit_hit(rows) == (None, None)
    for name in ("limit-tokens", "limit-error"):
        rows = rollout.tail_lines(ROLLOUTS / "rollout-2026-10-15T10-00-00-{}.jsonl".format(name))
        assert rollout.limit_hit(rows) == ("rate_limit", "turn-2")
    rows = rollout.tail_lines(ROLLOUTS / "rollout-2026-10-15T10-00-00-limit-earlier.jsonl")
    assert rollout.limit_hit(rows) == (None, None)


def test_rollout_paginated_item_completed_exit_code():
    rows = [{"type": "event_msg", "payload": {"type": "item_completed", "item": {
        "type": "CommandExecution", "id": "call_x", "exit_code": 3}}}]
    assert rollout.exit_code(rows, "call_x") == 3


def test_rollout_reader_is_bounded_and_refuses_other_files(tmp_path):
    other = tmp_path / "notes.jsonl"
    other.write_text('{"type":"event_msg","payload":{}}\n')
    assert rollout.tail_lines(other) == [] and rollout.tail_lines(None) == []
    assert rollout.tail_lines(tmp_path / "rollout-missing.jsonl") == []
    big = tmp_path / "rollout-big.jsonl"
    filler = json.dumps({"type": "response_item", "payload": {"type": "message", "text": "x" * 1000}})
    big.write_text("\n".join([filler] * 600 + [json.dumps({"type": "event_msg", "payload": {"type": "task_started"}})]) + "\n")
    rows = rollout.tail_lines(big)
    assert 200 < len(rows) < 300 and rows[-1]["payload"]["type"] == "task_started"


@pytest.mark.skipif(not shutil.which("zstd"), reason="needs the zstd binary")
def test_rollout_zst_via_binary(tmp_path):
    import subprocess
    source = ROLLOUTS / "rollout-2026-10-15T10-00-00-normal.jsonl"
    target = tmp_path / "rollout-cold.jsonl.zst"
    subprocess.run(["zstd", "-q", "-o", str(target), str(source)], check=True)
    # A long timeout: the hook's 150 ms limit is not what this test checks.
    assert rollout.exit_code(rollout.tail_lines(target, zstd_timeout=60), "call_fail") == 7


def test_rollout_zst_via_stdlib(tmp_path):
    zstd = pytest.importorskip("compression.zstd")  # Python 3.14+
    source = ROLLOUTS / "rollout-2026-10-15T10-00-00-normal.jsonl"
    target = tmp_path / "rollout-cold.jsonl.zst"
    target.write_bytes(zstd.compress(source.read_bytes()))
    assert rollout.exit_code(rollout.tail_lines(target), "call_fail") == 7


def real(name):
    path = payload_path("codex", name)
    return path.exists() and not json.loads(path.read_text()).get("doc_derived")


@pytest.mark.skipif(not (ROLLOUTS / "rollout-real-limit.jsonl").exists(), reason="no real Codex limit hit recorded yet")
def test_real_limit_hit_is_detected():
    reason, _ = rollout.limit_hit(rollout.tail_lines(ROLLOUTS / "rollout-real-limit.jsonl"))
    assert reason == "rate_limit"


def test_real_rollout_exit_codes():
    """Real rollout from a Codex 0.162.1 run (paginated history):
    exit codes come from item_completed CommandExecution items."""
    rows = rollout.tail_lines(ROLLOUTS / "rollout-real-session.jsonl")
    commands = {}
    for row in rows:
        item = row["payload"].get("item") or {}
        if row["payload"].get("type") == "item_completed" and item.get("type") == "CommandExecution":
            commands[item["id"]] = item["command"][-1]
    found = {cmd: rollout.exit_code(rows, ident) for ident, cmd in commands.items()}
    assert found['python3 -c "raise SystemExit(7)"'] == 7 and found["python3 hello.py"] == 0
    assert found['agent-autosave plan set --quiet "Write hello.py" "Run it" "Add a failing check"'] == 127
    assert rollout.limit_hit(rows) == (None, None)


def test_codex_without_plan_tool_uses_the_reply_checklist():
    message = "Plan:\n- [x] Write hello.py\n- [ ] Run it\n- [ ] Add a failing check"
    events, _ = run("Stop", data=dict(payload("Stop"), last_assistant_message=message))
    assert events[0]["kind"] == "plan" and events[0]["origin"] == "message"
    assert [i["state"] for i in events[0]["items"]] == ["done", "todo", "todo"]
