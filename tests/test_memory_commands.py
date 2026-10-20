"""note --keep, memory add/review/accept/reject/list/pin/forget, and status."""
import re
import json
from datetime import date, timedelta

import pytest

from agent_autosave.commands import memory as memory_command
from tests.memory_helpers import accept_all, finish_task, repo  # noqa: F401


@pytest.fixture
def person(monkeypatch):
    """A person at a terminal: memory add writes MEMORY.md directly."""
    monkeypatch.setattr(memory_command, "interactive", lambda: True)


def _memory(repo):
    return (repo.root / ".autosave/MEMORY.md").read_text()


def test_notes_become_candidates_at_done_and_never_go_straight_to_memory(repo):
    finish_task(repo)
    assert not (repo.root / ".autosave/MEMORY.md").exists()
    code, out = repo.cli("memory", "review")
    rows = out["data"]["pending"]
    assert code == 0 and [(r["type"], r["source"]["agent"]) for r in rows] == [("decision", "you"),
                                                                            ("tried", "you")]
    lines = (repo.root / ".autosave/memory/candidates.jsonl").read_text().splitlines()
    assert [json.loads(line)["v"] for line in lines] == [1, 1]


def test_the_review_screen_lists_every_candidate(repo):
    finish_task(repo)
    code, text = repo.text("memory", "review")
    assert code == 0 and "agent-autosave memory review  2 waiting" in text
    assert "Stream rows with a generator, not pandas" in text and "memory accept <id>" in text


def test_accept_writes_the_entry_with_its_source_and_a_block_id(repo):
    save = finish_task(repo)
    accept_all(repo)
    text = _memory(repo)
    decisions = text.split("## Decisions")[1].split("## Dead ends")[0]
    assert re.search(r"- Stream rows with a generator, not pandas, because billing tables are huge <!-- m:.* --> \^m-[0-9a-f]{6}\n", decisions)
    assert '"source":{"agent":"you","task_id":"' + save["task_id"] + '"}' in decisions
    assert "Loading the whole billing table" in text.split("## Dead ends")[1].split("## Gotchas")[0]
    assert repo.cli("memory", "review")[1]["data"]["pending"] == []


def test_accept_with_text_and_reject_with_a_reason(repo):
    finish_task(repo)
    first, second = [r["id"] for r in repo.cli("memory", "review")[1]["data"]["pending"]]
    assert repo.cli("memory", "accept", first, "--text", "Stream billing rows")[0] == 0
    code, out = repo.cli("memory", "reject", second, "--reason", "pandas works now")
    assert code == 0 and out["data"]["candidate"]["status"] == "rejected"
    assert re.search(r"- Stream billing rows <!-- m:.* --> \^m-", _memory(repo)) and "pandas ran out" not in _memory(repo)
    assert repo.cli("memory", "accept", second)[0] == 1  # already decided


def test_a_rejected_note_is_not_proposed_again(repo):
    finish_task(repo, tried=None)
    [row] = repo.cli("memory", "review")[1]["data"]["pending"]
    repo.cli("memory", "reject", row["id"])
    finish_task(repo, goal="Another export", tried=None)
    assert repo.cli("memory", "review")[1]["data"]["pending"] == []


def test_note_keep_proposes_right_away(repo):
    repo.prompt("Speed up the importer")
    code, out = repo.cli("note", "--keep", "--type", "warning", "CI has no network; mock the HTTP client")
    assert code == 0
    [row] = repo.cli("memory", "review")[1]["data"]["pending"]
    assert row["type"] == "gotcha" and row["source"]["agent"] == "you"
    code, _ = repo.cli("note", "--keep", "--type", "warning", "CI has no network; mock the HTTP client!")
    assert len(repo.cli("memory", "review")[1]["data"]["pending"]) == 1  # a duplicate is not proposed twice


def test_memory_add_from_an_agent_is_only_proposed(repo):
    code, out = repo.cli("memory", "add", "--type", "convention", "Tests live next to the module")
    assert code == 0 and out["data"]["accepted"] is False and out["data"]["candidate"]["type"] == "convention"


def test_memory_add_in_a_terminal_writes_it_from_you(repo, person):
    code, out = repo.cli("memory", "add", "--type", "convention", "Tests live next to the module")
    entry = out["data"]["entry"]
    assert code == 0 and entry["source"] == {"agent": "you", "task_id": None}
    assert re.search(r"- Tests live next to the module <!-- m:.* --> \^" + entry["id"] + "\n", _memory(repo))


def test_until_and_replaces(repo, person):
    old = repo.cli("memory", "add", "--type", "decision", "Use Redis for the cache")[1]["data"]["entry"]
    new = repo.cli("memory", "add", "--type", "decision", "Use SQLite for the cache", "--replaces", old["id"])
    assert new[0] == 0
    soon = (date.today() + timedelta(days=3)).isoformat()
    repo.cli("memory", "add", "--type", "gotcha", "Staging is down this week", "--until", soon)
    listed = [e["text"] for e in repo.cli("memory", "list")[1]["data"]["entries"]]
    assert listed == ["Use SQLite for the cache", "Staging is down this week"]
    every = [e["text"] for e in repo.cli("memory", "list", "--all")[1]["data"]["entries"]]
    assert "Use Redis for the cache" in every
    assert repo.cli("memory", "add", "--type", "gotcha", "x", "--until", "next week")[0] == 2
    assert repo.cli("memory", "add", "--type", "gotcha", "x", "--replaces", "m-000000")[0] == 1


def test_pin_and_forget(repo, person):
    entry = repo.cli("memory", "add", "--type", "gotcha", "Never run migrations on Friday")[1]["data"]["entry"]
    assert repo.cli("memory", "pin", entry["id"])[1]["data"]["entry"]["pinned"] is True
    assert '"pinned":true' in _memory(repo)
    assert repo.cli("memory", "forget", entry["id"])[0] == 0
    assert "migrations" not in _memory(repo)
    code, out = repo.cli("memory", "forget", entry["id"])
    assert code == 1 and out["error"]["code"] == "unknown_entry"


def test_status_shows_counts_and_the_waiting_hint_but_never_injects_it(repo):
    finish_task(repo)
    code, out = repo.cli("status")
    assert out["data"]["memory"]["candidates"] == 2
    assert "2 candidates waiting: run agent-autosave memory review" in repo.text("status")[1]
    context = repo.start("s2")
    assert "candidates waiting" not in context and "memory review" not in context
    accept_all(repo)
    assert repo.cli("status")[1]["data"]["memory"]["entries"] == {"decision": 1, "tried": 1, "gotcha": 0,
                                                                   "convention": 0}


def test_doctor_warns_past_max_entries(repo, person):
    from tests.memory_helpers import set_config
    set_config(repo, memory={"max_entries": 2})
    for text in ("one fact about ports", "two facts about hosts", "three facts about disks"):
        repo.cli("memory", "add", "--type", "gotcha", text)
    checks = repo.cli("doctor")[1]["data"]["checks"]
    [warning] = [c for c in checks if c["agent"] == "memory"]
    assert warning["level"] == "yellow" and "3 entries (memory.max_entries is 2)" in warning["message"]
