"""Replay a real Claude Code session (2026-10-15, macOS)."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from agent_autosave.capture import handle
from agent_autosave.installer.install import install
from agent_autosave.loader import context
from agent_autosave.normalize.claude_code import checklist
from agent_autosave.redact import shorten_home

from tests.helpers import LENIENT_CLI

SESSION = Path(__file__).resolve().parent / "fixtures/claude-code/sessions/lifecycle"
FIRST = "5145dfc5"
SYNC = {"AGENT_AUTOSAVE_SESSION_END": "sync"}  # no detached rebuilds in tests


def recordings():
    rows = [json.loads(p.read_text()) for p in SESSION.glob("*.json")]
    return [r["payload"] for r in sorted(rows, key=lambda r: r["recorded_at"])]


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "scratch"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master", str(root)], check=True)
    install(root, command="agent-autosave")
    return root


def play(project, until=None):
    for data in recordings():
        data = json.loads(json.dumps(data))
        if until and until(data):
            break
        data["cwd"] = str(project)
        inputs = data.get("tool_input")
        if isinstance(inputs, dict) and "file_path" in inputs:
            inputs["file_path"] = str(project / Path(inputs["file_path"]).name)
        handle("claude-code", data["hook_event_name"], json.dumps(data).encode(), env=SYNC)
    log = project / ".autosave/errors.log"
    assert not log.exists(), log.read_text()[-2000:]  # say which error, if one is logged
    return json.loads((project / ".autosave/save.json").read_text()), (project / ".autosave/SAVE.md").read_text()


def first_session_only(data):
    return not data["session_id"].startswith(FIRST) and data["hook_event_name"] == "SessionEnd"


def test_e2e_plan_comes_from_the_final_message_checklist(project):
    # No TodoWrite/TaskCreate call exists in the recording: Claude kept the
    # 3-step plan as a Markdown checklist in its Stop message.
    tools = {d.get("tool_name") for d in recordings()}
    assert not tools & {"TodoWrite", "TaskCreate", "TaskUpdate"}
    save, md = play(project, first_session_only)
    assert [i["state"] for i in save["plan"]] == ["done", "done", "todo"]
    assert save["plan"][0]["step"] == "Write `hello.py`"
    assert "## Plan\n- [x] Write \\`hello.py\\`" in md


def test_e2e_compact_then_clear_and_exit_is_paused(project):
    save, md = play(project, first_session_only)
    assert save["status"] == "paused" and save["interrupted"] is None
    assert "**Status:** paused," in md and "interrupted" not in md


def test_compact_source_session_start_clears_the_compact_interruption(project):
    save, _ = play(project, lambda d: d["hook_event_name"] == "SessionEnd")
    assert save["status"] == "active" and save["interrupted"] is None


def test_e2e_next_action_is_a_code_span_without_escapes(project):
    _, md = play(project, first_session_only)
    assert '## Next action\nMake `python3 -c "raise SystemExit(7)"` pass.' in md
    assert "\\`" not in md.split("## Next action")[1]


def test_e2e_files_touched_excludes_install_footprint(project):
    save, md = play(project, first_session_only)
    assert save["files_touched"] == ["hello.py"]
    assert "Files: hello.py." in md


def test_e2e_unborn_branch_says_no_commits_yet(project):
    save, md = play(project, first_session_only)
    assert "Branch master, no commits yet.\n" in md and "unborn" not in md  # no trailing space
    git = save["git"]
    assert git["head"] is None
    # Everything init wrote is hidden from the git lists, like files_touched.
    assert git["untracked"] == [] and git["dirty"] == []
    text = context(save)
    assert "no commits yet" in text and "uncommitted changes" not in text and "CLAUDE.md" not in text


def test_git_lists_keep_real_untracked_files_but_hide_footprint(project):
    (project / "notes.txt").write_text("x")
    save, _ = play(project, first_session_only)
    assert save["git"]["untracked"] == ["notes.txt"] and save["git"]["dirty"] == ["notes.txt"]
    assert "untracked" in context(save) and "notes.txt" in context(save)


def test_e2e_whole_recording_replays_to_paused(project):
    save, _ = play(project)
    assert save["status"] == "paused"


def test_checklist_parsing_is_strict_and_capped():
    assert checklist("x\n- [x] 1. **A**\n* [ ] B\n- [X] C\n  - [ ] D\n-[ ] E\n- [ ] 2) F\n") == [
        {"step": "A", "state": "done"}, {"step": "F", "state": "todo"}]
    assert checklist("no list here") == []
    assert len(checklist("\n".join("- [ ] s%d" % i for i in range(30)))) == 20


def test_shorten_home_handles_realpath_and_boundaries(monkeypatch, tmp_path):
    real = tmp_path / "real" / "khair"
    real.mkdir(parents=True)
    link = tmp_path / "link"
    os.symlink(str(tmp_path / "real"), str(link))
    monkeypatch.setenv("HOME", str(link / "khair"))
    monkeypatch.setenv("USERPROFILE", str(link / "khair"))
    value = {"a": [str(link / "khair/.claude/x.jsonl"), str(real) + "/p", str(real) + "ish", str(real)]}
    assert shorten_home(value) == {"a": ["~/.claude/x.jsonl", "~/p", str(real) + "ish", "~"]}


def test_committed_fixtures_have_no_home_paths():
    root = Path(__file__).resolve().parent / "fixtures"
    for path in root.rglob("*.json"):
        text = path.read_text()
        assert "/Users/" not in text and "/home/" not in text and "C:\\\\Users" not in text, path
        assert "[redacted:env]/.claude" not in text, path


def test_transcript_path_is_saved_with_home_shortened(project, monkeypatch):
    monkeypatch.setenv("HOME", str(project.parent))
    data = recordings()[0]
    data["cwd"] = str(project)
    data["transcript_path"] = str(project.parent / ".claude/projects/t.jsonl")
    handle("claude-code", "SessionStart", json.dumps(data).encode(), env={})
    assert str(project.parent) + "/.claude" not in (project / ".autosave/events.jsonl").read_text()
    assert "~/.claude/projects/t.jsonl" in (project / ".autosave/events.jsonl").read_text()


def _wait_for(predicate, seconds=10):
    import time
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_session_end_hook_rebuilds_in_background_quickly(project):
    import sys
    # Through the first session's last Stop: status active on disk.
    play(project, lambda d: d["hook_event_name"] == "PreCompact")
    end = next(d for d in recordings() if d["hook_event_name"] == "SessionEnd")
    end = dict(end, cwd=str(project), session_id=FIRST + "-x", reason="prompt_input_exit")
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_AUTOSAVE_")}
    result = subprocess.run([*LENIENT_CLI, "hook", "claude-code", "SessionEnd"],
                            input=json.dumps(end).encode(), cwd=str(project), env=env,
                            capture_output=True, timeout=10)
    assert result.returncode == 0 and result.stdout == b"" and result.stderr == b""
    # (How fast the hook returns is measured in the serial timing phase.)
    saved = project / ".autosave/save.json"
    assert _wait_for(lambda: json.loads(saved.read_text())["status"] == "paused")
    assert _wait_for(lambda: "**Status:** paused," in (project / ".autosave/SAVE.md").read_text())
    assert not (project / ".autosave/errors.log").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX process table")
def test_background_rebuild_leaves_no_zombie(project):
    from agent_autosave.background import spawn
    play(project, first_session_only)
    saved = project / ".autosave/save.json"
    saved.unlink()
    spawn(project)
    # The direct child (sh) is reaped by spawn(); none of ours remain.
    try:
        pid, _ = os.waitpid(-1, os.WNOHANG)
    except ChildProcessError:
        pid = 0
    assert pid == 0
    assert _wait_for(saved.exists)  # finished before tmp cleanup


def test_background_rebuild_logs_errors(tmp_path):
    from agent_autosave.background import main
    (tmp_path / ".autosave").mkdir()
    assert main([str(tmp_path)]) == 0
    assert (tmp_path / ".autosave/errors.log").exists()


def test_status_show_resume_recompile_first(project, run_cli):
    play(project, lambda d: d["hook_event_name"] == "PreCompact")
    from agent_autosave.state import TaskStore
    TaskStore(project).append({"v": 1, "ts": "2030-01-01T00:00:00Z", "agent": "claude-code", "session": "z",
                               "turn": 0, "kind": "session_end", "reason": "other"})
    for command in (["status"], ["show"], ["resume", "codex"]):
        (project / ".autosave/save.json").write_text(json.dumps({"status": "stale"}))
        run_cli(*command, "--json", cwd=project)
        assert json.loads((project / ".autosave/save.json").read_text())["status"] == "paused", command


def test_plan_cli_set_and_done(project, run_cli):
    result = run_cli("plan", "set", "Write hello.py", "Run it", "sk-ant-api03-" + "a" * 40, "--json", cwd=project)
    data = json.loads(result.stdout)
    assert result.returncode == 0 and [i["state"] for i in data["data"]["plan"]] == ["todo"] * 3
    ledger = (project / ".autosave/events.jsonl").read_text()
    assert "aaaaaaaaaa" not in ledger and '"plan_set"' in ledger  # redacted, append-only
    result = run_cli("plan", "done", "2", "--json", cwd=project)
    assert [i["state"] for i in json.loads(result.stdout)["data"]["plan"]] == ["todo", "done", "todo"]
    assert '"plan_done"' in (project / ".autosave/events.jsonl").read_text()
    assert json.loads(run_cli("plan", "done", "9", "--json", cwd=project).stdout)["ok"] is False
    save = json.loads((project / ".autosave/save.json").read_text())
    assert save["handoffs"] == [] and all(a["agent"] != "cli" for a in save["agents"])


def test_simultaneous_plan_done_processes_are_both_recorded(project, run_cli):
    import sys
    assert run_cli("plan", "set", "a", "b", "c", "d", cwd=project).returncode == 0
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_AUTOSAVE_")}
    env.pop("PYTHONPATH", None)
    procs = [subprocess.Popen([*LENIENT_CLI, "plan", "done", n, "--json"],
                              cwd=str(project), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
             for n in ("2", "4")]
    for proc in procs:
        out, _ = proc.communicate(timeout=20)
        assert proc.returncode == 0, out
    from agent_autosave.state import TaskStore
    save = TaskStore(project, lock_timeout=5).rebuild()
    assert [i["state"] for i in save["plan"]] == ["todo", "done", "todo", "done"]
    assert (project / ".autosave/events.jsonl").read_text().count('"plan_done"') == 2


def test_plan_cli_quiet_prints_nothing_when_piped(project, run_cli):
    result = run_cli("plan", "set", "--quiet", "a", "b", cwd=project)
    assert result.returncode == 0 and result.stdout == b"" and result.stderr == b""
    result = run_cli("--quiet", "plan", "done", "1", cwd=project)
    assert result.returncode == 0 and result.stdout == b""


def test_claude_block_tells_agents_to_run_our_commands_alone_and_quiet():
    text = (Path(__file__).resolve().parents[1] / "agent_autosave/resources/claude-code/CLAUDE.block.md").read_text()
    assert "never chained" in text and "--quiet" in text
