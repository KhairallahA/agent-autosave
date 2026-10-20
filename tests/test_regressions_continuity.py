"""Regression tests for the six continuity bugs, each reproduced through
capture.handle with recorded-shape payloads. Each one failed on main before
its fix (see docs/design/continuity.md)."""
from pathlib import Path

from agent_autosave.loader import context
from tests.helpers import FIXTURES
from tests.scenario import Repo, codex_recording

OAUTH = "Add OAuth login to the settings page with the Google provider"
CSV = "rewrite the CSV exporter so it streams rows instead of loading them all"


def test_f1_a_killed_session_hands_its_task_to_the_next_session(tmp_path):
    repo = Repo(tmp_path / "repo")
    repo.prompt(OAUTH, session="s1")
    repo.claude("PostToolUse-Edit", session="s1")
    repo.bash("pytest -q", exit=1, session="s1")
    # The terminal is killed: no Stop, no SessionEnd. A new session starts.
    text = repo.start("s2")
    assert "may still be running; the task is unfinished." in text and "pytest -q" in text


def test_f2_done_ends_a_task_without_a_plan(tmp_path):
    repo = Repo(tmp_path / "repo")
    repo.prompt(OAUTH)
    repo.stop("OAuth login works.")
    code, result = repo.cli("done", "--quiet")
    assert code == 0 and result["data"]["status"] == "done"
    repo.prompt(CSV)
    assert repo.save()["goal"] == CSV


def test_f2_a_clear_after_finished_work_starts_a_new_task(tmp_path):
    repo = Repo(tmp_path / "repo")
    repo.prompt(OAUTH)
    repo.stop("OAuth login works.")
    repo.end("s1")
    assert repo.start("s2", source="clear") == ""
    repo.prompt(CSV, session="s2")
    assert repo.save()["goal"] == CSV


def test_f2_the_latest_request_reaches_the_next_agent(tmp_path):
    repo = Repo(tmp_path / "repo")
    repo.prompt(OAUTH)
    repo.prompt(CSV)
    text = context(repo.save(), force=True)
    assert 'Latest request: "' + CSV in text


def test_f3_the_agents_last_message_is_kept(tmp_path):
    repo = Repo(tmp_path / "repo")
    repo.prompt(CSV)
    message = "Remaining: add a test for 1M rows; I chose a generator over pandas because of memory."
    repo.stop(message)
    assert message in repo.markdown()
    assert message in context(repo.save(), force=True)


def test_f4_remaining_steps_and_failing_output_reach_the_next_agent(tmp_path):
    repo = Repo(tmp_path / "repo")
    repo.prompt(CSV)
    repo.cli("plan", "set", "Stream rows with a generator", "Add a 1M-row test", "Update the docs")
    repo.bash("pytest tests/test_export.py -q", exit=1)
    text = context(repo.save(), force=True)
    assert "Add a 1M-row test" in text and "Update the docs" in text and '"boom"' in text


def test_f5_read_and_grep_never_crowd_out_real_work(tmp_path):
    repo = Repo(tmp_path / "repo")
    repo.prompt(CSV)
    repo.claude("PostToolUse-Edit")
    repo.tool("Read", {"file_path": str(repo.root / "export.py")})
    repo.tool("Grep", {"pattern": "def export"})
    repo.tool("Read", {"file_path": str(repo.root / "rows.py")})
    repo.stop()
    save = repo.save()
    assert not any(action in ("Read", "Grep") for action in save["last_actions"])
    assert save["files_read"] == ["rows.py", "export.py"]


def test_f6_hooks_delivered_twice_record_each_action_once(tmp_path):
    repo = Repo(tmp_path / "repo", agents=("claude-code",))
    folder = FIXTURES / "codex/sessions/plan-handoff"
    for path in sorted(folder.glob("*.json"), key=lambda p: codex_recording("plan-handoff/" + p.name).get("ts", p.name)):
        payload = codex_recording("plan-handoff/" + path.name)
        if payload["hook_event_name"] == "SessionEnd":
            continue
        for _ in range(2):
            repo.send("codex", payload, fresh_ids=False)
    save = repo.save()
    assert len(save["last_actions"]) == len(set(save["last_actions"])), save["last_actions"]
    assert not any(action.endswith(" x2") for action in save["last_actions"])
    assert len(save["steering"]) == len(set(save["steering"]))
