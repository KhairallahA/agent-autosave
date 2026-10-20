"""Duplicates: one hook delivered twice (two hook layers) is one event,
keyed by the agent's own id; doctor names the second layer."""
import json

from agent_autosave.compiler import compile_events
from agent_autosave.normalize import claude_code, codex, cursor
from tests.helpers import FIXTURES, event, load_payload, tool
from tests.scenario import Repo, codex_recording

TS = "2026-10-17T12:00:00Z"


def test_vendor_ids_come_from_the_payload_only():
    rows, _ = claude_code.normalize(load_payload("claude-code", "PostToolUse-Edit"), ts=TS, turn=1)
    assert rows[0]["id"] == load_payload("claude-code", "PostToolUse-Edit")["tool_use_id"]
    rows, _ = claude_code.normalize(load_payload("claude-code", "UserPromptSubmit"), ts=TS, turn=1)
    assert rows[0]["id"] == load_payload("claude-code", "UserPromptSubmit")["prompt_id"]
    prompt = codex_recording("plan-handoff/UserPromptSubmit-17be2b700d80.json")
    rows, _ = codex.normalize(prompt, ts=TS, turn=1)
    assert rows[0]["id"] == prompt["turn_id"]
    stop = codex_recording("plan-handoff/Stop-9f956362cdb2.json")
    rows, _ = codex.normalize(stop, ts=TS, turn=1)
    assert [r["id"] for r in rows if r["kind"] == "turn_end"] == [stop["turn_id"]]
    shell = load_payload("cursor", "postToolUse-Shell-fail")
    rows, _ = cursor.normalize(shell, ts=TS, turn=1)
    assert rows[0]["id"] == shell["tool_use_id"]
    rows, _ = cursor.normalize(load_payload("cursor", "beforeSubmitPrompt"), ts=TS, turn=1)
    assert rows[0]["id"] == load_payload("cursor", "beforeSubmitPrompt")["generation_id"]


def test_no_id_when_the_agent_sends_none():
    payload = dict(load_payload("claude-code", "PostToolUse-Edit"))
    del payload["tool_use_id"]
    rows, _ = claude_code.normalize(payload, ts=TS, turn=1)
    assert "id" not in rows[0]
    session_start, _ = claude_code.normalize(load_payload("claude-code", "SessionStart-startup"), ts=TS, turn=0)
    assert "id" not in session_start[0]


def test_the_compiler_skips_a_seen_agent_session_kind_id():
    twice = [tool(1, summary="pytest -q", exit=1, id="toolu_1"), tool(2, summary="pytest -q", exit=1, id="toolu_1")]
    assert compile_events(twice)["last_actions"] == ["Ran pytest -q (exit 1)"]
    again = [tool(1, summary="pytest -q", exit=1, id="toolu_1"), tool(2, summary="pytest -q", exit=1, id="toolu_2")]
    assert compile_events(again)["last_actions"] == ["Ran pytest -q (exit 1) x2"]  # two real runs
    other_session = [tool(1, summary="ls -z", exit=2, id="x"), dict(tool(2, summary="ls -z", exit=2, id="x"), session="b")]
    assert compile_events(other_session)["last_actions"] == ["Ran ls -z (exit 2) x2"]
    prompts = [event("prompt", 0, text="rewrite the exporter to stream", id="p1"),
               event("prompt", 1, text="also keep the old signature", id="p1")]
    assert compile_events(prompts)["steering"] == []


def test_codex_plan_handoff_delivered_twice_compiles_with_no_repeats(tmp_path):
    once, twice = Repo(tmp_path / "once"), Repo(tmp_path / "twice")
    folder = FIXTURES / "codex/sessions/plan-handoff"
    ordered = sorted(folder.glob("*.json"), key=lambda p: json.loads(p.read_text())["recorded_at"])
    for path in ordered:
        payload = json.loads(path.read_text())["payload"]
        if payload["hook_event_name"] == "SessionEnd":
            continue
        once.send("codex", payload, fresh_ids=False)
        for _ in range(2):
            twice.send("codex", payload, fresh_ids=False)
    a, b = once.save(), twice.save()
    for key in ("last_actions", "steering", "goal", "failing", "plan", "decisions", "last_report"):
        assert a[key] == b[key], key
    assert not any(action.endswith(" x2") for action in b["last_actions"])


def test_doctor_warns_on_two_claude_hook_layers(tmp_path, home):
    from agent_autosave.installer import doctor
    from agent_autosave.installer.install import install
    from tests.helpers import CMD
    project = tmp_path / "repo"
    project.mkdir()
    install(project, ("claude-code",), command=CMD)
    settings = (project / ".claude/settings.json").read_text()
    (project / ".claude/settings.local.json").write_text(settings)
    checks = [c for c in doctor.audit(project) if c["level"] == doctor.YELLOW and "settings.local.json" in c["message"]]
    assert checks, doctor.audit(project)
    assert "hint" in checks[0] and "settings.local.json" in checks[0]["hint"]
