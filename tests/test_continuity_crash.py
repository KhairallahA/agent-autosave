"""Crash takeover: a session that never ended hands its task to the next one."""
from tests.scenario import Repo

LINE = "Claude Code was last active at {} and may still be running; the task is unfinished."


def killed(tmp_path):
    repo = Repo(tmp_path / "repo")
    repo.prompt("Add OAuth login to the settings page", session="s1")
    repo.claude("PostToolUse-Edit", session="s1")
    repo.bash("pytest -q", exit=1, session="s1")
    return repo


def test_killed_session_is_injected_into_next_session(tmp_path):
    repo = killed(tmp_path)
    text = repo.start("s2")
    at = repo.save()["updated"][11:16]
    assert LINE.format(at + " UTC") in text and '"pytest -q"' in text


def test_resuming_the_same_session_injects_nothing(tmp_path):
    repo = killed(tmp_path)
    assert repo.start("s1", source="resume") == ""


def test_two_live_sessions_both_see_may_still_be_running(tmp_path):
    repo = killed(tmp_path)
    assert "may still be running" in repo.start("s2")
    repo.prompt("Also cover the callback route", session="s2")
    # s1 is still alive and now picks the work up again in a third terminal.
    assert "may still be running" in repo.start("s3")
