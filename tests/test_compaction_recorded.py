"""Against the shape Claude Code 2.1.302 really writes
(tests/fixtures/claude-code/transcripts/compact-2.1.302.jsonl): a
compact_boundary system line, then the isCompactSummary user line whose
parentUuid is the boundary's uuid and whose timestamp is EARLIER than the
boundary's. Matched by file order and parentUuid, never by timestamp."""
import json

from agent_autosave.compiler import compile_events
from agent_autosave.loader import context
from agent_autosave.transcripts import claude
from tests.helpers import FIXTURES, event, load_payload
from tests.scenario import Repo

RECORDED = FIXTURES / "claude-code/transcripts/compact-2.1.302.jsonl"
BOUNDARY, SUMMARY = [json.loads(line) for line in RECORDED.read_text().splitlines()]
NOISE = [{"type": "attachment", "uuid": "n1", "parentUuid": SUMMARY["uuid"], "attachment": {"type": "x"}},
         {"type": "last-prompt", "lastPrompt": "Summarise export.py"},
         {"type": "user", "uuid": "n2", "parentUuid": "n1", "message": {"role": "user", "content": "next prompt"}}]


def write(home, rows, name="bd1ed632.jsonl"):
    path = home / ".claude/projects/-private-tmp-aa-cont" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


def test_reads_the_recorded_shape(home):
    found = claude.compact_summary(write(home, [BOUNDARY, SUMMARY] + NOISE))
    assert found["text"] == SUMMARY["message"]["content"]
    assert found["at"] == BOUNDARY["timestamp"] == "2026-10-18T00:17:00.999Z"  # the boundary's, not the summary's
    assert (found["trigger"], found["pre_tokens"], found["post_tokens"]) == ("manual", 39427, 3362)
    assert found["boundary"] == BOUNDARY["uuid"]


def test_list_content_is_joined(home):
    summary = dict(SUMMARY, message={"role": "user", "content": [{"type": "text", "text": "first part"},
                                                                  {"type": "image"}, {"type": "text", "text": "second"}]})
    assert claude.compact_summary(write(home, [BOUNDARY, summary]))["text"] == "first part\nsecond"


def test_two_compactions_newest_wins(home):
    old_boundary = dict(BOUNDARY, uuid="old-b", timestamp="2026-10-18T00:30:00.000Z")  # later stamp, older line
    old_summary = dict(SUMMARY, uuid="old-s", parentUuid="old-b", message={"content": "the first compaction"})
    found = claude.compact_summary(write(home, [old_boundary, old_summary] + NOISE + [BOUNDARY, SUMMARY] + NOISE))
    assert found["text"] == SUMMARY["message"]["content"] and found["boundary"] == BOUNDARY["uuid"]


def test_matched_by_parent_then_by_file_order(home):
    stray = dict(SUMMARY, uuid="stray", parentUuid="someone-else", message={"content": "not this boundary's"})
    found = claude.compact_summary(write(home, [BOUNDARY, stray, SUMMARY]))
    assert found["text"] == SUMMARY["message"]["content"]  # parentUuid beats file order
    unparented = dict(SUMMARY, parentUuid=None)
    assert claude.compact_summary(write(home, [BOUNDARY] + NOISE[:1] + [unparented]))["text"] == SUMMARY["message"]["content"]


def test_a_boundary_without_its_summary_yet_reads_nothing(home):
    assert claude.compact_summary(write(home, [BOUNDARY])) is None
    before = dict(SUMMARY)  # a summary line BEFORE the boundary belongs to no newer compaction
    assert claude.compact_summary(write(home, [before, BOUNDARY])) is None


def test_the_save_keeps_tokens_trigger_and_a_word_boundary_cut():
    words = " ".join("word{}".format(i) for i in range(400))
    save = compile_events([event("prompt", 0, text="rewrite the CSV exporter to stream"),
                           event("summary", 1, text=words, at=BOUNDARY["timestamp"], trigger="manual",
                                 pre_tokens=39427, post_tokens=3362)])
    summary = save["summary"]
    assert len(summary["text"]) <= 1200 and summary["text"].endswith("\u2026")
    assert summary["text"][:-1].split()[-1] in words.split()
    assert (summary["at"], summary["trigger"], summary["pre_tokens"], summary["post_tokens"]) == \
        (BOUNDARY["timestamp"], "manual", 39427, 3362)


def test_compact_start_before_the_summary_is_written_then_stop_shows_it(tmp_path, home):
    repo = Repo(tmp_path / "repo")
    repo.prompt("rewrite the CSV exporter so it streams rows instead of loading them all")
    path = write(home, NOISE[1:])  # SessionStart(compact) fires before Claude writes the two lines
    payload = dict(load_payload("claude-code", "SessionStart-compact"), session_id="s1",
                   transcript_path=str(path), source="compact")
    repo.send("claude-code", payload)
    assert repo.save()["summary"] is None
    with path.open("a") as stream:
        stream.write("".join(json.dumps(row) + "\n" for row in [BOUNDARY, SUMMARY] + NOISE))
    repo.stop("Summarised export.py.")
    save = repo.save()
    assert save["summary"]["text"] == SUMMARY["message"]["content"] and save["summary"]["at"] == BOUNDARY["timestamp"]
    markdown = repo.markdown()
    assert "## Session summary (Claude Code compacted its context: 39.4k to 3.4k tokens)" in markdown
    line = next(l for l in context(save, force=True).splitlines() if l.startswith("Session summary"))
    assert len(json.loads(line[line.index('"'):-1])) <= 400
    repo.stop("Again.")  # read once: no second summary event
    rows = (repo.root / ".autosave/events.jsonl").read_text().splitlines()
    assert sum(json.loads(row)["kind"] == "summary" for row in rows) == 1
