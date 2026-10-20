import json

import pytest

from agent_autosave.state import TaskStore
from tests.helpers import START, event, tool


def test_init_clear_archive_latest_events_and_same_timestamp_boundaries(tmp_path):
    store = TaskStore(tmp_path, {})
    first = store.start(START)
    store.append(event("prompt", text="First task owns this substantive prompt."))
    store.append(tool(1, tool="Edit", summary="Edited a.py", files=["a.py"]))
    # Clear before a snapshot: the archive still includes the pending tool.
    second = store.start(START, reason="clear")
    assert first["task_id"] != second["task_id"]
    archived = json.loads((store.directory / "history" / (first["task_id"] + ".json")).read_text())
    assert archived["goal"] == "First task owns this substantive prompt."
    assert archived["files_touched"] == ["a.py"]
    assert second["goal"] == ""
    store.append(event("prompt", text="Second task owns this substantive prompt."))
    assert store.rebuild(git_state=None)["goal"] == "Second task owns this substantive prompt."
    assert len(store.ledger.read()) == 3  # older events survive in the ledger


def test_prompt_after_done_starts_and_archives_task_even_without_rebuild(tmp_path):
    store = TaskStore(tmp_path, {})
    first = store.start(START)
    store.append(event("prompt", text="Implement the first independent task."))
    store.append(event("plan", 1, items=[{"step": "ship", "state": "done"}]))
    store.append(event("turn_end", 2))
    store.append(event("session_start", 3, source="resume", agent="codex", session="next"))
    store.append(event("prompt", 4, text="Implement the second independent task.", agent="codex", session="next"))
    save = store.rebuild(git_state=None)
    assert save["task_id"] != first["task_id"]
    assert save["goal"] == "Implement the second independent task."
    assert save["status"] == "active"
    assert "plan" not in save
    archive = json.loads((store.directory / "history" / (first["task_id"] + ".json")).read_text())
    assert archive["status"] == "done"
    assert len(save["agents"]) == 1


def test_history_retains_newest_fifty(tmp_path):
    store = TaskStore(tmp_path, {})
    for index in range(53):
        store.start(event("turn_end", index)["ts"], reason="clear")
    archives = sorted((store.directory / "history").glob("t_*.json"))
    assert len(archives) == 50
    assert json.loads(archives[0].read_text())["started"] == event("turn_end", 2)["ts"]


def test_rebuild_repairs_stale_markdown_and_propagates_logged_errors(tmp_path, monkeypatch):
    store = TaskStore(tmp_path, {})
    store.start(START)
    store.append(event("prompt", text="Repair stale Markdown after a crash."))
    (store.directory / "SAVE.md").write_text("stale")
    save = store.rebuild(git_state=None)
    assert save["goal"] in (store.directory / "SAVE.md").read_text()
    (store.directory / "task.json").write_text('{"v": 99}')
    with pytest.raises(ValueError):
        store.rebuild(git_state=None)
    assert "task.rebuild" in (store.directory / "errors.log").read_text()


def test_rebuild_collects_git_at_compile_time(tmp_path, monkeypatch):
    store = TaskStore(tmp_path, {})
    store.start(START, git_state={"head": "a" * 40})
    calls = []
    def read(project, **kwargs):
        calls.append((project, kwargs))
        return {"head": "b" * 40, "dirty": ["changed.py"]}
    monkeypatch.setattr("agent_autosave.gitstate.read", read)
    assert store.rebuild()["git"]["head"] == "b" * 40
    assert calls[0][1]["timeout_ms"] == 150
    assert calls[0][1]["start_head"] == "a" * 40


def test_prompt_after_partial_append_repairs_before_boundary_detection(tmp_path):
    store = TaskStore(tmp_path, {})
    store.start(START)
    store.append(event("prompt", text="Preserve the current task after a crash."))
    with store.ledger.path.open("ab") as stream:
        stream.write(b'{"v":1,"partial":')
    store.append(event("prompt", 1, text="Continue with the repaired event ledger."))
    save = store.rebuild(git_state=None)
    assert save["goal"] == "Preserve the current task after a crash."
    assert save["steering"] == ["Continue with the repaired event ledger."]
    assert "ledger.repair" in (store.directory / "errors.log").read_text()
