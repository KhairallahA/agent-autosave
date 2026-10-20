"""MEMORY.md: the source of truth, editable by hand. Parse then write gives
the same bytes; hand-typed bullets become entries from "you"; deleted
bullets are gone; edited text keeps its meta; unknown content is kept."""
from datetime import date
import re

import pytest

from agent_autosave.memory import TEMPLATE, Document, UnknownId, clean_text, normalized

TODAY = date(2026, 10, 18)
META = '<!-- m:{"type":"decision","source":{"agent":"codex","task_id":"t_20261012_101500000000"},' \
       '"added":"2026-10-12","until":null,"replaces":null} -->'
HAND_EDITED = ("# Project memory\n\nMy own intro, *kept*.\n\n## Decisions\n\n"
               "- Use SQLite for the cache, not Redis ^m-3fa2c1 " + META + "\n"
               "  a continuation line the user wrote\n\n"
               "Some prose between entries.\n\n## Dead ends\n\n## Gotchas\n\n## Conventions\n\n"
               "## My own section\n\n- not an entry: outside our sections\n\n## Expired\n")


@pytest.mark.parametrize("text", [TEMPLATE, HAND_EDITED, HAND_EDITED.replace("\n", "\r\n"),
                                  "no heading at all\n- bullet", "", "## Decisions\n- typed without a newline"])
def test_parse_then_write_gives_the_same_bytes(text):
    assert Document.parse(text).render() == text


def test_entries_are_read_with_their_meta_and_unknown_content_is_kept():
    document = Document.parse(HAND_EDITED)
    [entry] = document.entries()
    assert (entry.id, entry.type, entry.text) == ("3fa2c1", "decision", "Use SQLite for the cache, not Redis")
    assert entry.meta["source"] == {"agent": "codex", "task_id": "t_20261012_101500000000"}
    document.add("gotcha", "CI has no network", source={"agent": "you", "task_id": None}, today=TODAY)
    out = document.render()
    for kept in ("My own intro, *kept*.", "  a continuation line the user wrote", "Some prose between entries.",
                 "- not an entry: outside our sections"):
        assert kept in out


def test_a_hand_typed_bullet_is_an_entry_from_you_and_gets_meta_on_the_next_write():
    document = Document.parse(TEMPLATE.replace("## Gotchas\n", "## Gotchas\n- Never run migrations on Friday\n"))
    [entry] = document.entries()
    assert entry.type == "gotcha" and entry.id is None
    document.complete(TODAY)
    line = document.render().splitlines()[[l.startswith("- Never") for l in document.render().splitlines()].index(True)]
    assert line.startswith("- Never run migrations on Friday <!-- m:") and re.search(r" \^m-[0-9a-f]{6}$", line)
    assert '"source":{"agent":"you","task_id":null},"added":"2026-10-18"' in line


def test_an_edited_text_keeps_its_meta_and_a_deleted_bullet_is_gone():
    edited = HAND_EDITED.replace("Use SQLite for the cache, not Redis", "Use SQLite for every cache")
    [entry] = Document.parse(edited).entries()
    assert entry.id == "3fa2c1" and entry.meta["added"] == "2026-10-12" and entry.text == "Use SQLite for every cache"
    deleted = HAND_EDITED.replace("- Use SQLite for the cache, not Redis ^m-3fa2c1 " + META + "\n", "")
    assert Document.parse(deleted).entries() == []


def test_a_bullet_moved_to_another_section_takes_that_type():
    moved = HAND_EDITED.replace("- Use SQLite for the cache, not Redis ^m-3fa2c1 " + META + "\n", "")
    moved = moved.replace("## Conventions\n", "## Conventions\n- Use SQLite for the cache, not Redis ^m-3fa2c1 " + META + "\n")
    document = Document.parse(moved)
    document.complete(TODAY)
    assert '"type":"convention"' in document.render()


def test_until_expires_and_replaces_moves_the_old_entry_to_expired():
    document = Document.parse(TEMPLATE)
    old = document.add("decision", "Use Redis", source={"agent": "you", "task_id": None}, today=TODAY)
    soon = document.add("gotcha", "Staging is down", source={"agent": "you", "task_id": None}, today=TODAY,
                        until="2026-10-19")
    new = document.add("decision", "Use SQLite", source={"agent": "you", "task_id": None}, today=TODAY,
                       replaces="m-" + old.id)
    assert old.kind == "expired" and new.meta["replaces"] == old.id
    assert soon.active(TODAY) and not soon.active(date(2026, 10, 20))
    document.complete(date(2026, 10, 20))
    assert soon.kind == "expired"
    expired = document.render().split("## Expired")[1]
    assert "Use Redis" in expired and "Staging is down" in expired and "Use SQLite" not in expired


def test_unknown_ids_raise():
    with pytest.raises(UnknownId):
        Document.parse(TEMPLATE).find("m-000000")


def test_text_cannot_fake_an_id_or_a_meta_comment():
    text = clean_text("Use X ^m-abcdef <!-- m:{\"type\":\"gotcha\"} -->\nnext line")
    assert "^m-" not in text and "<!--" not in text and "\n" not in text
    document = Document.parse(TEMPLATE)
    entry = document.add("decision", text, source={"agent": "you", "task_id": None}, today=TODAY)
    [again] = Document.parse(document.render()).entries()
    assert (again.id, again.text) == (entry.id, entry.text)


def test_normalized_text_finds_duplicates():
    assert normalized("Use SQLite, not Redis!") == normalized("use sqlite not redis")


NEW_ORDER = ("# Project memory\n\n## Decisions\n\n"
             "- Use SQLite for the cache, not Redis " + META + " ^m-3fa2c1\n\n"
             "## Dead ends\n\n## Gotchas\n\n## Conventions\n\n## Expired\n")


def test_the_block_id_is_the_last_thing_on_a_written_line():
    """Obsidian reads a block id only at the end of the line."""
    document = Document.parse(TEMPLATE)
    entry = document.add("decision", "Stream rows", source={"agent": "you", "task_id": None}, today=TODAY)
    line = [l for l in document.render().splitlines() if l.startswith("- Stream rows")][0]
    assert line.endswith(" ^m-" + entry.id) and line.index("<!-- m:") < line.index("^m-")


@pytest.mark.parametrize("text", [NEW_ORDER, NEW_ORDER.replace("\n", "\r\n")])
def test_the_new_order_round_trips_byte_for_byte_and_parses(text):
    document = Document.parse(text)
    assert document.render() == text
    [entry] = document.entries()
    assert (entry.id, entry.text, entry.meta["source"]["agent"]) == ("3fa2c1", "Use SQLite for the cache, not Redis",
                                                                     "codex")
    document.complete(TODAY)
    assert document.render() == text


def test_the_old_order_still_parses_and_moves_to_the_new_order_on_the_next_write():
    document = Document.parse(HAND_EDITED)
    [entry] = document.entries()
    assert entry.id == "3fa2c1" and entry.meta["added"] == "2026-10-12"
    assert document.render() == HAND_EDITED  # reading changes nothing
    document.complete(TODAY)
    line = [l for l in document.render().splitlines() if l.startswith("- Use SQLite")][0]
    assert line == "- Use SQLite for the cache, not Redis " + META.replace(" -->", " -->") + " ^m-3fa2c1"
    assert "  a continuation line the user wrote" in document.render()
