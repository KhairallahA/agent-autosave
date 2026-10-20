"""Journal pages: one Markdown page per finished task, Obsidian-friendly."""
import json
import re

from agent_autosave import journal
from tests.memory_helpers import accept_all, finish_task, repo  # noqa: F401


def _page(repo):
    [path] = sorted((repo.root / ".autosave/journal").rglob("*.md"))
    return path, path.read_text()


def test_a_finished_task_gets_a_page_at_the_documented_path(repo):
    save = finish_task(repo)
    path, page = _page(repo)
    relative = path.relative_to(repo.root / ".autosave").as_posix()
    assert re.fullmatch(r"journal/\d{4}/\d\d/\d{4}-\d\d-\d\d-add-a-csv-export-for-the-billing-rows-\d{6}\.md", relative)
    from agent_autosave.memory_sync import local_zone
    assert relative == journal.relative_path(save, tz=local_zone())


def test_the_frontmatter_is_json_quoted_yaml_with_every_field(repo):
    save = finish_task(repo)
    _, page = _page(repo)
    head = page.split("---\n")[1]
    fields = {line.split(": ", 1)[0]: json.loads(line.split(": ", 1)[1]) for line in head.strip().splitlines()}
    assert list(fields) == ["title", "task_id", "status", "date", "branch", "agents", "started", "ended",
                            "commits_since_start", "files_changed", "tags"]
    assert fields["task_id"] == save["task_id"] and fields["status"] == "done"
    assert fields["agents"] == ["claude-code"] and fields["tags"] == ["agent-autosave", "task"]
    assert journal.read_frontmatter(page)["title"] == "Add a CSV export for the billing rows"


def test_the_sections_are_in_order_and_hold_the_task(repo):
    finish_task(repo)
    _, page = _page(repo)
    headings = re.findall(r"(?m)^## (.+)$", page)
    assert headings == ["Goal", "Outcome", "Plan", "Decisions", "Dead ends", "Checks", "Failing at the end", "Files",
                        "Handoffs", "Session summary", "Memory"]
    assert "Stream rows with a generator" in journal.section_text(page, ("Decisions",))
    assert "ran out of memory" in journal.section_text(page, ("Dead ends",))
    assert "pytest -q" in journal.section_text(page, ("Checks",))


def test_accepted_entries_are_linked_back_with_block_ids(repo):
    finish_task(repo)
    accept_all(repo)
    repo.cli("journal")  # any memory command syncs
    _, page = _page(repo)
    ids = re.findall(r"\^m-([0-9a-f]{6})", (repo.root / ".autosave/MEMORY.md").read_text())
    assert ids and all("[[MEMORY#^m-{}]]".format(i) in page for i in ids)


def test_a_locked_page_is_never_rewritten(repo):
    finish_task(repo)
    path, page = _page(repo)
    locked = page.replace("tags:", "locked: true\ntags:", 1) + "\nMy own notes.\n"
    path.write_text(locked)
    accept_all(repo)  # would add links to an unlocked page
    repo.cli("journal")
    assert path.read_text() == locked and journal.is_locked(path)


def test_journal_lists_searches_and_finds_a_path(repo):
    save = finish_task(repo)
    code, out = repo.cli("journal")
    [row] = out["data"]["pages"]
    assert code == 0 and row["task_id"] == save["task_id"] and row["status"] == "done"
    assert repo.cli("journal", "--search", "PANDAS")[1]["data"]["pages"] == [row]
    assert repo.cli("journal", "--search", "kubernetes")[1]["data"]["pages"] == []
    code, out = repo.cli("journal", "--path", save["task_id"])
    assert code == 0 and out["data"]["path"] == row["path"]
    assert repo.cli("journal", "--path", "t_19990101_000000000000")[0] == 1


def test_history_shows_the_journal_page(repo):
    finish_task(repo)
    repo.prompt("Something else entirely")  # archives the finished task
    code, out = repo.cli("history")
    [archived] = out["data"]["saves"]
    assert archived["journal"].startswith(".autosave/journal/")


def test_two_tasks_with_one_goal_started_in_the_same_second_get_two_pages():
    first = {"task_id": "t_20261018_101500000000", "goal": "Fix it", "started": "2026-10-18T10:15:00Z"}
    second = dict(first, task_id="t_20261018_101500000001")
    taken = {journal.relative_path(first)}
    assert journal.relative_path(second, taken) == "journal/2026/10/2026-10-18-fix-it-101500-2.md"
    assert journal.relative_path(second, taken | {"journal/2026/10/2026-10-18-fix-it-101500-2.md"}).endswith("-3.md")


def test_slugs_are_plain_ascii():
    assert journal.slug("Ünïcode / slashes \\ and ../dots!") == "unicode-slashes-and-dots"


UTC_PLUS_3 = __import__("datetime").timezone(__import__("datetime").timedelta(hours=3))


def _late_save():
    """A task started at 22:30 UTC: already the next day at UTC+3."""
    return {"task_id": "t_20261018_223005123456", "goal": "Add a CSV export for the billing rows",
            "started": "2026-10-18T22:30:05.123456Z", "updated": "2026-10-18T23:40:00.000000Z", "status": "done"}


def test_journal_dates_are_local_around_midnight_utc():
    """A real macOS run (UTC+3): the page was dated 2026-10-18 (UTC)
    and its memory entry 2026-10-19 (local), so Claude called the journal
    "the day before"."""
    save = _late_save()
    relative = journal.relative_path(save, tz=UTC_PLUS_3)
    assert relative == "journal/2026/10/2026-10-19-add-a-csv-export-for-the-billing-rows-013005.md"
    head = journal.frontmatter(save, tz=UTC_PLUS_3)
    assert head["date"] == "2026-10-19"
    assert head["started"] == "2026-10-18T22:30:05.123456Z" and head["ended"] == "2026-10-18T23:40:00.000000Z"
    page = journal.render_page(save, [], {}, tz=UTC_PLUS_3)
    from agent_autosave import recall
    assert recall.journal_doc(relative, page)["date"] == "2026-10-19"
    # Without a zone (the pure default) the UTC day is kept.
    assert journal.relative_path(save).startswith("journal/2026/10/2026-10-18-")


def test_page_memory_entry_and_recall_share_the_local_date(repo, monkeypatch):
    import datetime as dt
    import time
    if not hasattr(time, "tzset"):
        import pytest
        pytest.skip("needs time.tzset")
    # A zone where today's local date differs from the UTC date.
    hour = dt.datetime.now(dt.timezone.utc).hour
    monkeypatch.setenv("TZ", "Etc/GMT-13" if hour >= 11 else "Etc/GMT+12")
    time.tzset()
    try:
        local = dt.date.today().isoformat()
        assert local != dt.datetime.now(dt.timezone.utc).date().isoformat()
        finish_task(repo)
        accept_all(repo)
        path, page = _page(repo)
        assert path.name.startswith(local) and journal.read_frontmatter(page)["date"] == local
        memory = (repo.root / ".autosave/MEMORY.md").read_text()
        assert '"added":"{}"'.format(local) in memory
        code, out = repo.cli("journal")
        assert out["data"]["pages"][0]["date"] == local
    finally:
        monkeypatch.undo()
        time.tzset()
