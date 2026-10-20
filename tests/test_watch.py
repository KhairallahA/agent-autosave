"""`agent-autosave watch`: one frame (--once), the plain redraw loop and q."""
import json

from agent_autosave.commands import watch
from agent_autosave.installer.install import install


def test_once_shows_status_usage_last_action_failing_and_candidates(tmp_path, run_cli):
    install(tmp_path, ["claude-code"], command="agent-autosave", statusline="off")
    run_cli("plan", "set", "write the test", "fix the bug")
    run_cli("memory", "propose", "--type", "decision", "Use SQLite for the cache.")
    result = run_cli("watch", "--once")  # piped: the JSON contract
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)["data"]
    assert data["plan_total"] == 2 and data["candidates"] == 1 and data["failing"] is None
    text = "\n".join(watch.frame(watch.snapshot(tmp_path)))
    assert text.startswith("agent-autosave watch")
    assert "plan 0/2" in text and "Memory    1 candidate waiting" in text and "Failing   nothing" in text


def test_the_frame_lists_every_slot_and_usage_row():
    lines = watch.frame({"status": "active", "goal": "g", "updated": None, "plan_done": 1, "plan_total": 3,
                         "slots": [{"branch": "main", "current": True, "task_id": "t_1_1", "status": "active"},
                                   {"branch": "feature", "current": False, "task_id": "t_2_1", "status": "paused"}],
                         "usage": ["Claude Code 72% of 5-hour limit"], "last_action": "Ran pytest",
                         "failing": "pytest", "candidates": 0}, width=60)
    text = "\n".join(lines)
    assert "* main" in text and "  feature" in text and "Claude Code 72%" in text and "Failing   pytest" in text
    assert all(len(line) < 60 for line in lines)


def test_the_plain_loop_redraws_until_q(tmp_path, monkeypatch, capsys):
    install(tmp_path, ["claude-code"], command="agent-autosave", statusline="off")
    keys = iter(["", "", "q"])
    watch._plain_loop(tmp_path, lambda timeout: next(keys))
    assert capsys.readouterr().out.count("agent-autosave watch") == 3


def test_without_autosave_it_fails_like_other_commands(tmp_path, run_cli):
    assert run_cli("watch", "--once").returncode != 0


def test_the_screen_shows_events_recorded_since_the_last_rebuild(tmp_path):
    """watch read save.json as the last turn end left it, so a reading the
    status line recorded mid-turn (or a tool call) did not show until then."""
    from agent_autosave import statusline
    install(tmp_path, ["claude-code"], command="agent-autosave", statusline="off")
    payload = {"cwd": str(tmp_path), "rate_limits": {"five_hour": {"used_percentage": 72}}}
    statusline.render(json.dumps(payload).encode())
    text = "\n".join(watch.frame(watch.snapshot(tmp_path)))
    assert "72%" in text, text
