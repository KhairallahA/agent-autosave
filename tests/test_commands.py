"""status, show, resume, save, note, history, clear, handoffs, stale saves,
and the settle wait after an agent quits."""
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from agent_autosave import cli
from agent_autosave.commands import common, resume as resume_command
from agent_autosave.compiler import compile_events
from agent_autosave.installer.install import install
from agent_autosave.render import render
from agent_autosave.state import TaskStore

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "tests/fixtures/codex/ledgers/passing-run"
GIT_ENV = dict(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def git(root, *args):
    env = dict(os.environ, **GIT_ENV)
    return subprocess.run(["git", *args], cwd=root, env=env, check=True, capture_output=True, text=True).stdout


def run(monkeypatch, capsys, root, *args):
    """Piped stdout: the CLI prints the JSON contract (--json is implied)."""
    monkeypatch.chdir(root)
    code = cli.main(list(args))
    out = capsys.readouterr().out.strip().splitlines()
    return code, json.loads(out[-1])


def event(kind, ts, agent="claude-code", session="s1", turn=1, **fields):
    return dict({"v": 1, "ts": ts, "agent": agent, "session": session, "turn": turn, "kind": kind}, **fields)


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_autosave.background.spawn", lambda project: None)
    root = tmp_path / "repo"
    root.mkdir()
    install(root, agents=("claude-code",), command="agent-autosave")
    return root


def seed(root, *events):
    store = TaskStore(root)
    for item in events:
        store.append(item)
    return store.rebuild(git_state=None)


def stamp(seconds=0):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) + timedelta(milliseconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def handoff_events():
    # After the task start that install() just recorded (offsets in ms).
    return [event("prompt", stamp(1), text="Fix the failing login test in src/auth.py"),
            event("interrupted", stamp(2), reason="rate_limit"),
            event("prompt", stamp(3), agent="codex", session="c1", text="continue"),
            event("session_end", stamp(4), agent="codex", session="c1", reason="other")]


# --- status and handoffs

def test_status_reports_handoffs_and_contract(project, monkeypatch, capsys):
    seed(project, *handoff_events())
    code, out = run(monkeypatch, capsys, project, "status")
    assert code == 0 and out["ok"]
    data = out["data"]
    assert data["status"] == "paused"
    assert data["goal"].startswith("Fix the failing login test")
    assert data["handoff_count"] == 1
    hand = data["handoffs"][0]
    assert (hand["from"], hand["to"], hand["reason"]) == ("claude-code", "codex", "rate_limit")
    assert {"plan_done", "plan_total", "next_action"} <= set(data)


def test_status_text_shows_handoff_count(project):
    save = seed(project, *handoff_events())
    monkey = common.handoff_text(save["handoffs"])
    assert monkey.startswith("Handoffs: handed off 1 time")
    assert "Claude Code -> Codex" in monkey
    two = common.handoff_text(save["handoffs"] * 2)
    assert "handed off 2 times" in two


def test_status_without_autosave_is_not_found(tmp_path, monkeypatch, capsys):
    code, out = run(monkeypatch, capsys, tmp_path, "status")
    assert code == 1 and out["error"]["code"] == "not_initialized"


# --- stale saves

@needs_git
def test_moved_head_is_flagged_stale_with_commit_count(project, monkeypatch, capsys):
    git(project, "init", "-q")
    git(project, "commit", "-q", "--allow-empty", "-m", "one")
    store = TaskStore(project)
    store.append(handoff_events()[0])
    store.rebuild()  # the hook's rebuild records HEAD
    code, out = run(monkeypatch, capsys, project, "status")
    assert out["data"]["stale"] is False
    git(project, "commit", "-q", "--allow-empty", "-m", "two")
    git(project, "commit", "-q", "--allow-empty", "-m", "three")
    for _ in range(2):  # status itself must not erase the evidence
        code, out = run(monkeypatch, capsys, project, "status")
        assert out["data"]["stale"] is True
        assert out["data"]["stale_reason"] == "This save may be stale: 2 commits landed since."
    code, out = run(monkeypatch, capsys, project, "show", "--context")
    assert out["data"]["context"].splitlines()[0] == "[agent-autosave resume context]"
    assert out["data"]["context"].splitlines()[1] == "This save may be stale: 2 commits landed since."


def test_old_save_is_flagged_stale(project):
    from agent_autosave.loader import stale_reason
    from datetime import datetime
    save = seed(project, *handoff_events())
    from datetime import timedelta
    updated = datetime.fromisoformat(save["updated"].replace("Z", "+00:00"))
    assert stale_reason(save, now=updated + timedelta(hours=47, minutes=5)) == "This save may be stale: it is 47 hours old."
    assert stale_reason(save, now=updated + timedelta(hours=1)) == ""


# --- show

def test_show_formats(project, monkeypatch, capsys):
    seed(project, *handoff_events())
    _, md = run(monkeypatch, capsys, project, "show")
    assert md["data"]["markdown"] == (project / ".autosave/SAVE.md").read_text()
    _, js = run(monkeypatch, capsys, project, "show", "--json")
    assert js["data"] == json.loads((project / ".autosave/save.json").read_text())
    _, ctx = run(monkeypatch, capsys, project, "show", "--context")
    assert "Fix the failing login test" in ctx["data"]["context"]
    assert len(ctx["data"]["context"]) <= 8000


def test_show_md_and_context_together_is_a_usage_error(project, monkeypatch, capsys):
    code, out = run(monkeypatch, capsys, project, "show", "--md", "--context")
    assert code == 2 and not out["ok"]


# --- resume

@pytest.mark.parametrize("agent,binary", [("claude-code", "claude"), ("codex", "codex")])
def test_resume_dry_run_prints_the_same_block_as_show_context(project, monkeypatch, capsys, agent, binary):
    seed(project, *handoff_events())
    _, ctx = run(monkeypatch, capsys, project, "show", "--context")
    code, out = run(monkeypatch, capsys, project, "resume", agent, "--dry-run")
    assert code == 0 and out["data"]["dry_run"] is True
    command = out["data"]["command"]
    assert Path(command[0]).name == binary
    assert command[1] == ctx["data"]["context"]


@pytest.mark.parametrize("agent,binary", [("claude-code", "claude"), ("codex", "codex")])
def test_resume_launches_each_agent_with_the_save(project, monkeypatch, capsys, agent, binary):
    seed(project, *handoff_events())
    calls = []
    monkeypatch.setattr(resume_command.shutil, "which", lambda name: "/opt/bin/" + name)
    monkeypatch.setattr(resume_command.subprocess, "call", lambda command, cwd=None: calls.append((command, cwd)) or 0)
    code, out = run(monkeypatch, capsys, project, "resume", agent)
    assert code == 0 and out["data"]["exit"] == 0
    (command, cwd), = calls
    assert command[0] == "/opt/bin/" + binary
    assert "Fix the failing login test" in command[1] and "paused" not in command[0]
    assert Path(cwd) == project.resolve()


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell scripts")
def test_resume_really_execs_the_agent_binary(project, monkeypatch, capsys, tmp_path):
    seed(project, *handoff_events())
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "codex"
    fake.write_text('#!/bin/sh\nprintf "%s" "$1" > "$PWD/received.txt"\nexit 7\n')
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    code, out = run(monkeypatch, capsys, project, "resume", "codex")
    assert code == 0 and out["data"]["exit"] == 7
    assert "Fix the failing login test" in (project / "received.txt").read_text()


def test_resume_agent_not_installed_is_not_found(project, monkeypatch, capsys):
    seed(project, *handoff_events())
    monkeypatch.setattr(resume_command.shutil, "which", lambda name: None)
    code, out = run(monkeypatch, capsys, project, "resume", "codex")
    assert code == 1 and out["error"]["code"] == "agent_not_installed"


def test_resume_nothing_unfinished_is_not_found(project, monkeypatch, capsys):
    code, out = run(monkeypatch, capsys, project, "resume", "codex", "--dry-run")
    assert code == 1 and out["error"]["code"] == "nothing_to_resume"
    seed(project, event("prompt", stamp(1), text="Make the build pass on every platform"),
         event("plan", stamp(2), items=[{"step": "a", "state": "done"}]),
         event("turn_end", stamp(3)))
    code, out = run(monkeypatch, capsys, project, "resume", "claude-code", "--dry-run")
    assert code == 1


def test_resume_unsupported_agent_is_invalid_args(project, monkeypatch, capsys):
    seed(project, *handoff_events())
    code, out = run(monkeypatch, capsys, project, "resume", "cursor", "--dry-run")
    assert code == 2 and out["error"]["code"] == "not_supported"


# --- note and save

def test_note_types_and_cli_is_never_a_handoff(project, monkeypatch, capsys):
    seed(project, handoff_events()[0])
    run(monkeypatch, capsys, project, "note", "Keep cookie sessions until v2")
    run(monkeypatch, capsys, project, "note", "Add a migration test", "--type", "todo")
    code, out = run(monkeypatch, capsys, project, "note", "Do not touch prod", "--type", "warning")
    assert code == 0
    save = json.loads((project / ".autosave/save.json").read_text())
    assert save["decisions"] == ["Keep cookie sessions until v2"]
    assert save["notes"] == [{"type": "todo", "text": "Add a migration test"},
                             {"type": "warning", "text": "Do not touch prod"}]
    assert save["handoffs"] == [] and [a["agent"] for a in save["agents"]] == ["claude-code"]
    md = (project / ".autosave/SAVE.md").read_text()
    assert "- warning: Do not touch prod" in md and "- Keep cookie sessions until v2" in md


def test_note_is_redacted_before_write(project, monkeypatch, capsys):
    seed(project, handoff_events()[0])
    secret = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    run(monkeypatch, capsys, project, "note", "token is " + secret)
    for name in ("events.jsonl", "save.json", "SAVE.md"):
        assert secret not in (project / ".autosave" / name).read_text()


def test_empty_note_is_invalid_args(project, monkeypatch, capsys):
    code, out = run(monkeypatch, capsys, project, "note", "   ")
    assert code == 2


def test_save_rebuilds_and_records_note(project, monkeypatch, capsys):
    seed(project, handoff_events()[0])
    code, out = run(monkeypatch, capsys, project, "save", "--note", "Chose OAuth2 PKCE")
    assert code == 0 and out["data"]["status"] == "active"
    assert json.loads((project / ".autosave/save.json").read_text())["decisions"] == ["Chose OAuth2 PKCE"]


# --- clear and history

def test_clear_archives_and_history_lists_newest_first(project, monkeypatch, capsys):
    seed(project, *handoff_events())
    first = json.loads((project / ".autosave/save.json").read_text())["task_id"]
    time.sleep(0.002)
    code, out = run(monkeypatch, capsys, project, "clear")
    assert code == 0 and out["data"]["archived"] == first
    assert (project / ".autosave/history" / (first + ".json")).is_file()
    new = json.loads((project / ".autosave/save.json").read_text())
    assert new["goal"] == "" and new["task_id"] != first
    seed(project, event("prompt", stamp(1), text="Second task that is long enough"))
    time.sleep(0.002)
    run(monkeypatch, capsys, project, "clear")
    (project / ".autosave/history/events-abc.jsonl").write_text("")  # rotation archives are not saves
    _, out = run(monkeypatch, capsys, project, "history")
    saves = out["data"]["saves"]
    assert len(saves) == 2 and saves[1]["task_id"] == first
    assert saves[0]["goal"] == "Second task that is long enough"
    assert saves[1]["agents"] == ["claude-code", "codex"] and saves[1]["handoff_count"] == 1
    _, out = run(monkeypatch, capsys, project, "history", "--limit", "1")
    assert len(out["data"]["saves"]) == 1


def test_history_empty(project, monkeypatch, capsys):
    code, out = run(monkeypatch, capsys, project, "history")
    assert code == 0 and out["data"]["saves"] == []


def test_quiet_success_prints_nothing(project, monkeypatch, capsys):
    seed(project, *handoff_events())
    monkeypatch.chdir(project)
    for args in (["status"], ["note", "x y z decision"], ["history"], ["show", "--context"]):
        assert cli.main(["--quiet", *args]) == 0
        assert capsys.readouterr().out == ""


# --- settle wait after /quit

def _age(path, seconds):
    t = time.time() - seconds
    os.utime(path, (t, t))


def test_settle_waits_for_a_late_session_end_write(project):
    from datetime import datetime, timezone
    seed(project, handoff_events()[0])
    directory = project / ".autosave"
    for name in ("save.json", "SAVE.md"):
        _age(directory / name, 5)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    with open(directory / "events.jsonl", "a") as handle:
        handle.write(json.dumps(event("session_end", ts, reason="other")) + "\n")

    def late_writer():
        time.sleep(0.3)
        for name in ("save.json", "SAVE.md"):
            os.utime(directory / name, None)
    worker = threading.Thread(target=late_writer)
    started = time.monotonic()
    worker.start()
    assert common.wait_for_settle(directory) is True
    waited = time.monotonic() - started
    worker.join()
    assert 0.25 <= waited < 1.0


def test_settle_gives_up_after_one_second(project):
    seed(project, handoff_events()[0])
    directory = project / ".autosave"
    for name in ("save.json", "SAVE.md"):
        _age(directory / name, 60)
    with open(directory / "events.jsonl", "a") as handle:
        handle.write(json.dumps(event("session_end", "2099-01-01T00:00:00Z", reason="other")) + "\n")
    clock = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds
    assert common.wait_for_settle(directory, sleep=sleep, clock=lambda: clock[0]) is True
    assert 0.95 <= sum(sleeps) <= 1.05 and all(s == common.SETTLE_POLL for s in sleeps)


def test_settle_does_not_wait_when_fresh_or_no_session_end(project):
    seed(project, handoff_events()[0])
    directory = project / ".autosave"
    no_sleep = lambda s: pytest.fail("must not wait")
    assert common.wait_for_settle(directory, sleep=no_sleep) is False  # no session_end
    store = TaskStore(project)
    store.end_session([event("session_end", stamp(5), reason="other")], git_state=None)
    assert common.wait_for_settle(directory, sleep=no_sleep) is False  # views written after it


@pytest.mark.parametrize("command", [["status"], ["show"], ["show", "--json"], ["resume", "codex", "--dry-run"]])
def test_status_show_resume_wait_for_session_end(project, monkeypatch, capsys, command):
    seed(project, handoff_events()[0])
    calls = []
    real = common.wait_for_settle
    monkeypatch.setattr(common, "wait_for_settle", lambda d, **k: calls.append(Path(d)) or real(d, **k))
    run(monkeypatch, capsys, project, *command)
    assert calls == [project.resolve() / ".autosave"]


# --- a real Codex run that ends green is a passing save

def check_events():
    return [json.loads(l) for l in (CHECK / "events.jsonl").read_text().splitlines() if l.strip()]


def test_check_3915296_compiles_to_the_recorded_paused_save():
    recorded = json.loads((CHECK / "save.json").read_text())
    save = compile_events(check_events(), recorded["git"], started=recorded["started"], task_id=recorded["task_id"])
    assert save["status"] == "paused"
    assert save["updated"] == [e for e in check_events() if e["kind"] == "session_end"][-1]["ts"]
    for key in ("status", "goal", "handoffs", "last_session", "plan", "decisions", "failing", "agents"):
        assert save.get(key) == recorded.get(key), key
    assert "**Status:** paused" in render(save)


def test_check_3915296_in_a_project_status_and_resume(project, monkeypatch, capsys):
    recorded = json.loads((CHECK / "save.json").read_text())
    directory = project / ".autosave"
    shutil.copy(CHECK / "events.jsonl", directory / "events.jsonl")
    (directory / "task.json").write_text(json.dumps({"v": 1, "task_id": recorded["task_id"],
                                                     "started": recorded["started"],
                                                     "cursor": {"offset": 0, "identity": None}, "start_head": None}))
    (directory / "save.json").write_text(json.dumps(recorded))
    (directory / "SAVE.md").write_text(render(recorded))
    code, out = run(monkeypatch, capsys, project, "status")
    assert code == 0 and out["data"]["status"] == "paused" and out["data"]["handoff_count"] == 2
    code, out = run(monkeypatch, capsys, project, "resume", "claude-code", "--dry-run")
    assert code == 0 and "The session ended at" in out["data"]["command"][1]
