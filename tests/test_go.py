"""`agent-autosave go` and `resume --next`: the switch chain."""
from datetime import datetime, timedelta, timezone
import json

from agent_autosave.commands import go
from agent_autosave.contract import ExitCode, success
from agent_autosave.installer.install import install
from agent_autosave.state import TaskStore

from tests.helpers import LENIENT_CLI

NOW = "2026-10-19T11:02:00Z"


def stamp(seconds):
    """Now plus seconds: the compiler skips events from before the task began."""
    when = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    return when.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def seed(tmp_path, *, agents=("claude-code", "codex"), status="interrupted",
         reason="rate_limit", usage=None, last="claude-code"):
    install(tmp_path, list(agents), command="agent-autosave", statusline="off", mcp=False)
    store = TaskStore(tmp_path)
    store.append({"v": 1, "ts": stamp(1), "agent": last, "session": "s1", "turn": 0,
                  "kind": "prompt", "text": "Fix the flaky login test in tests/test_auth.py"})
    store.append({"v": 1, "ts": stamp(2), "agent": last, "session": "s1", "turn": 1,
                  "kind": "plan_set", "steps": ["reproduce", "fix"]})
    if usage is not None:
        store.append({"v": 1, "ts": stamp(3), "agent": last, "session": "s1", "turn": 2, "kind": "usage",
                      "limit_pct": usage, "limit_window_minutes": 300,
                      "limit_resets_at": "2026-10-19T16:00:00Z", "weekly_pct": None, "context_pct": None})
    if status == "interrupted":
        store.append({"v": 1, "ts": stamp(4), "agent": last, "session": "s1", "turn": 3,
                      "kind": "interrupted", "reason": reason})
    elif status == "done":
        store.append({"v": 1, "ts": stamp(4), "agent": "cli", "session": "cli", "turn": 0, "kind": "task_done"})
    store.rebuild()
    return tmp_path


def test_should_switch_on_limit_overload_or_98_percent():
    assert go.should_switch({"status": "interrupted", "interrupted": {"reason": "rate_limit"}}) == "rate_limit"
    assert go.should_switch({"status": "interrupted", "interrupted": {"reason": "overloaded"}}) == "overloaded"
    assert go.should_switch({"status": "paused", "last_session": {"agent": "claude-code"},
                             "usage": {"claude-code": {"limit_pct": 98}}}) == "usage"
    assert go.should_switch({"status": "paused", "last_session": {"agent": "claude-code"},
                             "usage": {"claude-code": {"limit_pct": 97}}}) is None
    assert go.should_switch({"status": "done"}) is None


def test_the_prompt_names_the_agents_and_local_time():
    text = go.prompt_text("claude-code", "codex", "2026-10-19T11:02:00Z", "rate_limit")
    assert text.startswith("Claude Code hit its usage limit at ")
    assert text.endswith("Press Enter to continue in Codex, or q to stop.")
    assert "was overloaded" in go.prompt_text("claude-code", "codex", None, "overloaded")


def test_order_uses_named_agents_then_config_then_install(tmp_path):
    seed(tmp_path)
    assert go.order_of(tmp_path, ["codex", "claude-code"]) == ["codex", "claude-code"]
    assert go.order_of(tmp_path) == ["claude-code", "codex"]
    (tmp_path / ".autosave/config.json").write_text('{"v": 1, "switch": {"order": ["codex", "claude-code"]}}')
    assert go.order_of(tmp_path) == ["codex", "claude-code"]


def test_go_dry_run_prints_the_first_launch(tmp_path, run_cli):
    seed(tmp_path)
    result = run_cli("go", "claude-code", "codex", "--dry-run")
    assert result.returncode == 0
    data = json.loads(result.stdout)["data"]
    assert data["order"] == ["claude-code", "codex"] and data["steps"][0]["agent"] == "claude-code"


def test_enter_continues_to_the_next_agent(tmp_path, monkeypatch):
    seed(tmp_path)
    monkeypatch.chdir(tmp_path)
    launched = []

    def fake_launch(project, agent, dry_run=False):
        launched.append(agent)
        return success({"agent": agent, "exit": 0}), ExitCode.SUCCESS, agent + " exited."

    original = fake_launch

    def fake_launch(project, agent, dry_run=False):  # noqa: F811 - Codex finishes the task
        if agent == "codex":
            TaskStore(project).append({"v": 1, "ts": stamp(10), "agent": "cli", "session": "cli", "turn": 0,
                                       "kind": "task_done"})
        return original(project, agent, dry_run)

    monkeypatch.setattr(go, "launch", fake_launch)
    monkeypatch.setattr(go, "ask", lambda text, auto=False, read_key=None: True)
    from argparse import Namespace
    outcome = go.run(Namespace(agents=["claude-code", "codex"], dry_run=False, auto=False))
    assert launched == ["claude-code", "codex"]
    assert outcome[0]["ok"] and outcome[0]["data"]["status"] == "done"


def test_q_stops_the_chain(tmp_path, monkeypatch):
    seed(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(go, "launch", lambda *a, **k: (
        success({"agent": "claude-code", "exit": 0}), ExitCode.SUCCESS, "done"))
    monkeypatch.setattr(go, "ask", lambda *a, **k: False)
    from argparse import Namespace
    outcome = go.run(Namespace(agents=["claude-code", "codex"], dry_run=False, auto=False))
    assert outcome[0]["data"].get("stopped") is True


def test_auto_countdown_cancels_on_a_key():
    keys = iter(["x"])
    assert go.ask("prompt", auto=True, read_key=lambda t: next(keys, "")) is False
    keys = iter(["", "", "", "", ""])
    assert go.ask("prompt", auto=True, read_key=lambda t: next(keys, "")) is True


def test_resume_next_picks_the_agent_after_the_last_one(tmp_path, run_cli):
    seed(tmp_path)
    result = run_cli("resume", "--next", "--dry-run")
    assert result.returncode == 0
    data = json.loads(result.stdout)["data"]
    assert data["agent"] == "codex"


def test_a_finished_task_ends_the_chain(tmp_path, monkeypatch):
    seed(tmp_path)
    monkeypatch.chdir(tmp_path)
    launched = []

    def fake(project, agent, dry_run=False):
        launched.append(agent)
        TaskStore(project).append({"v": 1, "ts": stamp(10), "agent": "cli", "session": "cli", "turn": 0,
                                   "kind": "task_done"})
        return success({"agent": agent, "exit": 0}), ExitCode.SUCCESS, "ok"

    monkeypatch.setattr(go, "launch", fake)
    from argparse import Namespace
    outcome = go.run(Namespace(agents=["claude-code", "codex"], dry_run=False, auto=False))
    assert launched == ["claude-code"]
    assert outcome[0]["data"]["status"] == "done"


FAKE = '''#!{python}
import sys
from datetime import datetime, timezone
from agent_autosave.state import TaskStore
ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
event = {kind}
event.update(v=1, ts=ts, turn=9)
TaskStore(".").append(event)
'''


def test_go_hands_off_through_real_processes(tmp_path, run_cli, monkeypatch):
    """Fake `claude` hits its limit; Enter starts fake `codex`, which finishes."""
    import os
    import sys
    seed(tmp_path, status="active")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, kind in (("claude", '{"agent": "claude-code", "session": "s1", "kind": "interrupted", '
                                  '"reason": "rate_limit"}'),
                       ("codex", '{"agent": "cli", "session": "cli", "kind": "task_done"}')):
        path = bin_dir / name
        path.write_text(FAKE.format(python=sys.executable, kind=kind))
        path.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    root = str(__import__("pathlib").Path(__file__).resolve().parents[1])
    monkeypatch.setenv("PYTHONPATH", root)
    import subprocess
    result = subprocess.run([*LENIENT_CLI, "go", "claude-code", "codex"],
                            cwd=str(tmp_path), input=b"\n", capture_output=True, timeout=20,
                            env=dict(os.environ))
    assert result.returncode == 0, result.stderr
    assert b"Claude Code hit its usage limit at " in result.stderr
    assert b"Press Enter to continue in Codex, or q to stop." in result.stderr
    data = json.loads(result.stdout)["data"]
    assert [step["agent"] for step in data["steps"]] == ["claude-code", "codex"] and data["status"] == "done"


def test_go_starts_the_first_agent_without_a_prompt_when_there_is_no_task(tmp_path, monkeypatch):
    """`go claude-code codex` at the start of a task failed with "There is no
    unfinished save to resume": the first agent must start anyway."""
    from argparse import Namespace
    from agent_autosave.commands import resume
    install(tmp_path, ["claude-code", "codex"], command="agent-autosave", statusline="off", mcp=False)
    monkeypatch.chdir(tmp_path)
    ran = []
    monkeypatch.setattr(resume.shutil, "which", lambda name: "/usr/local/bin/" + name)
    monkeypatch.setattr(resume.subprocess, "call", lambda command, cwd=None: ran.append(command) or 0)
    outcome = go.run(Namespace(agents=["claude-code", "codex"], dry_run=False, auto=False))
    assert outcome[0]["ok"], outcome
    assert ran == [["/usr/local/bin/claude"]]
    dry = go.run(Namespace(agents=["claude-code", "codex"], dry_run=True, auto=False))
    assert dry[0]["data"]["steps"][0]["command"] == ["/usr/local/bin/claude"]
