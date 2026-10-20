"""SessionEnd and lifecycle repeats, replayed from the real recordings and
ledger in tests/fixtures/codex/sessions/resume-quit/ (codex-cli 0.162.1)."""
import json
from pathlib import Path
import signal

import pytest

from agent_autosave import capture
from agent_autosave.compiler import compile_events
from agent_autosave.installer.install import install
from agent_autosave.render import render
from tests.helpers import load_envelope

ROOT = Path(__file__).resolve().parents[1]
SESSION = ROOT / "tests/fixtures/codex/sessions/resume-quit"
OWNER_CWD = "/private/tmp/aa-codex"
SLASH = "/compact Reply with just: ok"


def ledger():
    return [json.loads(line) for line in (SESSION / "events.jsonl").read_text().splitlines() if line.strip()]


def codex_part():
    return [e for e in ledger() if e["agent"] != "claude-code"]


def compiled(events):
    return compile_events(events, started=events[0]["ts"], task_id=events[0]["task_id"])


def recordings():
    return sorted((json.loads(p.read_text()) for p in SESSION.glob("*.json")), key=lambda r: r["recorded_at"])


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setattr("agent_autosave.background.spawn", lambda project: None)
    root = tmp_path / "aa-codex"
    root.mkdir()
    install(root, agents=("claude-code", "codex"), command="agent-autosave")
    return root


# --- 1: SessionEnd settles save.json and SAVE.md inside the hook

def test_replayed_19da45a_run_says_paused_on_disk_after_every_session_end(project):
    ends = 0
    for rec in recordings():
        p = json.loads(json.dumps(rec["payload"]).replace(OWNER_CWD, str(project)))
        p.pop("transcript_path", None)
        capture.handle("codex", p["hook_event_name"], json.dumps(p).encode(), env={})
        if p["hook_event_name"] != "SessionEnd":
            continue
        ends += 1
        save = json.loads((project / ".autosave/save.json").read_text())
        rows = [json.loads(x) for x in (project / ".autosave/events.jsonl").read_text().splitlines()]
        assert rows[-1]["kind"] == "session_end"
        assert save["updated"] == rows[-1]["ts"], "save.json was not rebuilt after session_end"
        assert save["status"] == "paused"
        assert "**Status:** paused" in (project / ".autosave/SAVE.md").read_text()
    assert ends == 3
    assert not (project / ".autosave/errors.log").exists()


def test_session_end_hook_ignores_hangup_and_terminate(monkeypatch):
    seen = {}
    monkeypatch.setattr(signal, "signal", lambda sig, handler: seen.__setitem__(sig, handler))
    capture.main("codex", "SessionEnd", stdin=_Bytes(b"{}"))
    assert seen[signal.SIGHUP] is signal.SIG_IGN and seen[signal.SIGTERM] is signal.SIG_IGN
    seen.clear()
    capture.main("codex", "Stop", stdin=_Bytes(b"{}"))
    assert seen == {}


class _Bytes:
    def __init__(self, data):
        self.data = data

    def read(self, n):
        return self.data[:n]


# --- 2: lifecycle idempotency (compiler, so old ledgers are fixed too)

def test_real_resume_keeps_its_own_session_end():
    """The two session_ends and the two "ok" turn_ends of the resumed Codex
    session are separate quits/turns (turn 3 vs 4); none may be dropped."""
    events = codex_part()
    resumed = [i for i, e in enumerate(events) if e.get("source") == "resume"][0]
    assert compiled(events[:resumed + 2])["status"] == "active"  # resumed, prompted
    save = compiled(events)
    assert save["status"] == "paused" and save["last_session"]["ended"] is True
    assert save["updated"] == "2026-10-15T14:35:24.690052Z"


def _with_repeat(events, kind, seconds=".999999Z"):
    last = max(i for i, e in enumerate(events) if e["kind"] == kind)
    copy = dict(events[last], ts=events[last]["ts"][:19] + seconds)
    return events[:last + 1] + [copy] + events[last + 1:]


def test_repeated_session_end_and_turn_end_are_dropped():
    events = codex_part()
    base = compiled(events)
    doubled = _with_repeat(_with_repeat(events, "session_end"), "turn_end")
    assert len(doubled) == len(events) + 2
    assert compiled(doubled) == base
    full = ledger()
    assert compiled(_with_repeat(full, "session_end")) == compiled(full)


def test_a_different_turn_end_is_not_a_repeat():
    events = codex_part()
    last = max(i for i, e in enumerate(events) if e["kind"] == "turn_end")
    other = dict(events[last], ts=events[last]["ts"][:19] + ".999999Z", last_message="done, and more")
    events = events[:last + 1] + [other] + events[last + 1:]
    plain = compiled(events[:last + 2])
    assert plain["updated"] == other["ts"]


# --- 3: slash-command prompts stay in the ledger, not in steering

def test_slash_prompt_is_in_the_ledger_but_not_in_steering():
    assert any(e.get("text") == SLASH for e in ledger())
    save = compiled(ledger())
    assert save["steering"] == ['Run python3 hello.py. Then run python3 -c "raise SystemExit(7)" '
                                "and stop there; do not fix it.",
                                "What did the agent-autosave context at session start tell you?"]
    assert "/compact" not in json.dumps(save["steering"])
    assert SLASH not in render(save)


def test_resume_fixture_is_the_real_recording():
    named = load_envelope("codex", "SessionStart-resume")
    assert named == json.loads((SESSION / "SessionStart-a3af2f1b9847.json").read_text())
    assert named["payload"]["source"] == "resume" and not named.get("doc_derived")
    compact = load_envelope("codex", "SessionStart-compact")
    assert compact.get("doc_derived") is True
