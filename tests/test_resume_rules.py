"""Resume must not archive, empty
tasks are not resumable, history settles, junk is ignored, warnings in context."""
import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from agent_autosave import cli
from agent_autosave.compiler import _ignored, is_resume_block
from agent_autosave.config import DEFAULTS
from agent_autosave.installer.install import install
from agent_autosave.loader import MARKER, context, resumable
from agent_autosave.state import TaskStore

GIT_ENV = dict(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def stamp(ms=0):
    return (datetime.now(timezone.utc) + timedelta(milliseconds=ms)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def event(kind, ts=None, agent="claude-code", session="s1", turn=1, **fields):
    return dict({"v": 1, "ts": ts or stamp(), "agent": agent, "session": session, "turn": turn, "kind": kind}, **fields)


def run(monkeypatch, capsys, root, *args):
    monkeypatch.chdir(root)
    code = cli.main(list(args))
    out = capsys.readouterr().out.strip().splitlines()
    return code, (json.loads(out[-1]) if out else None)


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_autosave.background.spawn", lambda project: None)
    root = tmp_path / "repo"
    root.mkdir()
    install(root, agents=("claude-code", "codex"), command="agent-autosave")
    return root


def seed(root, *events, git_state=None):
    store = TaskStore(root)
    for item in events:
        store.append(item)
    return store.rebuild(git_state=git_state)


# --- 1: resume block never starts a new task / never becomes goal ---

def test_marker_is_first_line_of_every_context_block(project):
    save = seed(project, event("prompt", stamp(1), text="Create add.py that fails a test on purpose"))
    block = context(save, force=True)
    assert block.startswith(MARKER + "\n")
    assert is_resume_block(block)
    assert not is_resume_block("Create add.py that fails a test on purpose")


def test_resume_block_prompt_keeps_same_task_and_never_becomes_goal(project, monkeypatch, capsys):
    save = seed(project, event("prompt", stamp(1), text="Create add.py with a failing pytest"),
                event("session_end", stamp(2), reason="other"))
    task_id = save["task_id"]
    assert save["status"] == "paused" and save["goal"].startswith("Create add.py")
    block = context(save, force=True)
    # Simulate what `resume` injects as the agent's first prompt.
    store = TaskStore(project)
    store.append(event("prompt", stamp(3), agent="codex", session="new", text=block))
    store.append(event("session_start", stamp(4), agent="codex", session="new", source="startup"))
    store.append(event("session_end", stamp(5), agent="codex", session="new", reason="other"))
    after = store.rebuild()
    assert after["task_id"] == task_id
    assert after["goal"].startswith("Create add.py")
    assert MARKER not in after["goal"]
    assert all(MARKER not in s for s in after.get("steering") or [])
    assert after["status"] == "paused"
    history = list((project / ".autosave/history").glob("t_*.json")) if (project / ".autosave/history").is_dir() else []
    assert history == [], "resume must not archive the real task"
    code, out = run(monkeypatch, capsys, project, "history")
    assert out["data"]["saves"] == []


def test_resume_block_does_not_start_a_task_even_when_save_is_done(project):
    save = seed(project,
                event("prompt", stamp(1), text="Make the build pass on every platform"),
                event("plan", stamp(2), items=[{"step": "a", "state": "done"}]),
                event("turn_end", stamp(3)))
    assert save["status"] == "done"
    task_id = save["task_id"]
    block = context(save, force=True)
    store = TaskStore(project)
    store.append(event("prompt", stamp(4), agent="codex", session="r1", text=block))
    after = store.rebuild()
    assert after["task_id"] == task_id and after["status"] == "active"
    assert after["goal"].startswith("Make the build")
    assert not (project / ".autosave/history").exists() or list((project / ".autosave/history").glob("t_*.json")) == []


# --- 2: empty tasks not resumable; late SessionEnd after clear stays on old task ---

def test_empty_task_is_not_resumable_and_session_start_injects_nothing(project, monkeypatch, capsys):
    empty = json.loads((project / ".autosave/save.json").read_text())
    assert empty["goal"] == "" and empty["status"] == "active"
    assert resumable(empty) is False
    assert context(empty) == ""
    code, out = run(monkeypatch, capsys, project, "resume", "codex", "--dry-run")
    assert code == 1 and out["error"]["code"] == "nothing_to_resume"


def test_late_session_end_after_clear_does_not_make_new_task_unfinished(project, monkeypatch, capsys):
    save = seed(project, event("prompt", stamp(1), text="Create add.py with a failing pytest", agent="codex", session="c1"),
                event("tool", stamp(2), agent="codex", session="c1", tool="Bash", summary="pytest", exit=1,
                      files=[], output_tail="FAILED"))
    old_id = save["task_id"]
    run(monkeypatch, capsys, project, "clear")
    new = json.loads((project / ".autosave/save.json").read_text())
    assert new["task_id"] != old_id and new["goal"] == ""
    # Codex's SessionEnd from the pre-clear session arrives late.
    TaskStore(project).end_session(
        [event("session_end", stamp(3), agent="codex", session="c1", reason="other")], git_state=None)
    current = json.loads((project / ".autosave/save.json").read_text())
    assert current["task_id"] == new["task_id"]
    assert current["goal"] == "" and current["status"] == "active"
    assert current.get("agents") in ([], None)
    assert resumable(current) is False
    code, out = run(monkeypatch, capsys, project, "resume", "codex", "--dry-run")
    assert code == 1
    archived = json.loads((project / ".autosave/history" / (old_id + ".json")).read_text())
    assert archived["status"] == "paused"
    assert archived["updated"]  # late session_end recompiled it


def test_late_turn_end_after_clear_is_routed_to_archived_task(project, monkeypatch, capsys):
    save = seed(project, event("prompt", stamp(1), text="Create add.py with a failing pytest", agent="codex", session="c1"))
    old_id = save["task_id"]
    run(monkeypatch, capsys, project, "clear")
    TaskStore(project).append(event("turn_end", stamp(2), agent="codex", session="c1", last_message="ok"))
    current = json.loads((project / ".autosave/save.json").read_text())
    assert current["goal"] == "" and current.get("agents") in ([], None)
    rows = [json.loads(l) for l in (project / ".autosave/events.jsonl").read_text().splitlines() if l.strip()]
    late = [r for r in rows if r["kind"] == "turn_end"][-1]
    assert late["task_id"] == old_id


# --- 3: history shows final status, never active ---

def test_history_shows_paused_not_active_after_clear(project, monkeypatch, capsys):
    seed(project, event("prompt", stamp(1), text="Create add.py with a failing pytest"),
         event("session_end", stamp(2), reason="other"))
    run(monkeypatch, capsys, project, "clear")
    _, out = run(monkeypatch, capsys, project, "history")
    assert out["data"]["saves"][0]["status"] == "paused"


def test_history_settles_active_to_paused_even_without_session_end(project, monkeypatch, capsys):
    # Mid-task clear: status was still active.
    seed(project, event("prompt", stamp(1), text="Create add.py with a failing pytest"))
    assert json.loads((project / ".autosave/save.json").read_text())["status"] == "active"
    run(monkeypatch, capsys, project, "clear")
    _, out = run(monkeypatch, capsys, project, "history")
    assert out["data"]["saves"][0]["status"] == "paused"


# --- 4: junk paths ignored ---

def test_default_ignore_paths_cover_junk():
    paths = DEFAULTS["capture"]["ignore_paths"]
    for pattern in ("__pycache__/", "*.pyc", ".DS_Store", "node_modules/"):
        assert pattern in paths
    for sample in ("__pycache__/x.pyc", "pkg/__pycache__/a.pyc", "mod.pyc", "src/.DS_Store",
                   ".DS_Store", "node_modules/left-pad/index.js", "app/node_modules/x"):
        assert _ignored(sample, paths), sample
    assert not _ignored("src/add.py", paths)
    assert not _ignored("tests/test_add.py", paths)


@needs_git
def test_junk_not_in_git_line_or_files_touched(project):
    # Plant junk on disk so git status would see it, then rebuild.
    for path in ("__pycache__/x.pyc", "mod.pyc", ".DS_Store", "node_modules/x/y.py", "add.py"):
        target = project / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x")
    env = dict(os.environ, **GIT_ENV)
    subprocess.run(["git", "init", "-q"], cwd=project, env=env, check=True)
    subprocess.run(["git", "add", "add.py"], cwd=project, env=env, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "a"], cwd=project, env=env, check=True)
    # Leave junk untracked / dirty.
    seed(project, event("prompt", stamp(1), text="Create add.py with a failing pytest"),
         event("tool", stamp(2), tool="Write", summary="wrote cache", exit=0,
               files=["__pycache__/x.pyc", "mod.pyc", ".DS_Store", "add.py"]))
    save = json.loads((project / ".autosave/save.json").read_text())
    md = (project / ".autosave/SAVE.md").read_text()
    for junk in ("__pycache__", "mod.pyc", ".DS_Store", "node_modules"):
        assert junk not in (save.get("git") or {}).get("dirty", [])
        assert junk not in (save.get("git") or {}).get("untracked", [])
        assert not any(junk in f for f in save.get("files_touched") or [])
        assert junk not in md
    assert "add.py" in save["files_touched"]


# --- 5: warnings and to-dos in the context block ---

def test_context_lists_warnings_then_decisions_then_todos(project):
    save = seed(project, event("prompt", stamp(1), text="Create add.py with a failing pytest"))
    store = TaskStore(project)
    store.append(event("note", stamp(2), agent="cli", session="cli", turn=0,
                       text="Keep add() signature", type="decision"))
    store.append(event("note", stamp(3), agent="cli", session="cli", turn=0,
                       text="Do not touch CI", type="warning"))
    store.append(event("note", stamp(4), agent="cli", session="cli", turn=0,
                       text="Add a migration test", type="todo"))
    save = store.rebuild(git_state=(json.loads((project / ".autosave/save.json").read_text()).get("git")))
    block = context(save, force=True)
    assert 'Warnings: "Do not touch CI"' in block
    assert 'Decisions: "Keep add() signature"' in block
    assert 'To-dos: "Add a migration test"' in block
    # Warnings come before decisions/to-dos (most prominent).
    assert block.index("Warnings:") < block.index("Decisions:") < block.index("To-dos:")


# --- end-to-end of the reported flow ---

def test_resume_quit_clear_resume_is_not_found(project, monkeypatch, capsys):
    save = seed(project, event("prompt", stamp(1), text="Create add.py with a failing pytest", agent="codex", session="c1"),
                event("note", stamp(2), agent="cli", session="cli", turn=0, text="Do not touch CI", type="warning"),
                event("session_end", stamp(3), agent="codex", session="c1", reason="other"))
    block = context(save, force=True)
    assert "Warnings:" in block
    # resume injects the block; same task continues.
    store = TaskStore(project)
    store.append(event("prompt", stamp(4), agent="codex", session="c2", text=block))
    store.append(event("session_end", stamp(5), agent="codex", session="c2", reason="other"))
    mid = store.rebuild()
    assert mid["task_id"] == save["task_id"] and mid["goal"].startswith("Create add.py")
    run(monkeypatch, capsys, project, "clear")
    # Late SessionEnd from the resumed session (c2 was after clear's previous.sessions).
    # c2 started AFTER clear's previous snapshot... actually c2 was in the archived task.
    TaskStore(project).end_session(
        [event("session_end", stamp(6), agent="codex", session="c2", reason="other")], git_state=None)
    code, out = run(monkeypatch, capsys, project, "resume", "codex", "--dry-run")
    assert code == 1
    _, hist = run(monkeypatch, capsys, project, "history")
    assert hist["data"]["saves"][0]["status"] in ("paused", "interrupted", "done")
    assert hist["data"]["saves"][0]["status"] != "active"
