"""Compaction summary: the summary an agent wrote when it compacted, read
from its own transcript; never rebuilt from history."""
import json
import shutil

from agent_autosave.loader import context
from agent_autosave.transcripts import claude, codex as rollout
from tests.helpers import FIXTURES, load_payload
from tests.scenario import Repo, codex_recording

COMPACT = FIXTURES / "claude-code/transcripts/compact.jsonl"


def transcript(home):
    path = home / ".claude/projects/-repo/session.jsonl"
    path.parent.mkdir(parents=True)
    shutil.copy(str(COMPACT), str(path))
    return path


def test_reads_the_newest_compact_summary(home):
    found = claude.compact_summary(transcript(home))
    text = found["text"]
    assert found["boundary"] == "b1" and found["trigger"] == "auto" and found["pre_tokens"] == 180000
    assert text.startswith("This session is being continued") and "a 1M-row test is not written yet." in text


def test_reads_nothing_outside_claude_projects_or_without_the_marker(home, tmp_path):
    outside = tmp_path / "x.jsonl"
    shutil.copy(str(COMPACT), str(outside))
    assert claude.compact_summary(outside) is None
    path = transcript(home)
    assert claude.compact_summary(path.with_suffix(".txt")) is None
    path.write_text(COMPACT.read_text().replace('"isCompactSummary": true', '"isCompactSummary": false'))
    assert claude.compact_summary(path) is None
    assert claude.compact_summary(None) is None and claude.compact_summary(str(home / "missing.jsonl")) is None


def test_reads_only_the_tail(home):
    path = transcript(home)
    with path.open("a") as stream:
        stream.write(json.dumps({"type": "user", "message": {"content": "x" * (600 * 1024)}}) + "\n")
    assert claude.compact_summary(path) is None  # the summary is older than the 512 KB tail


def test_claude_compact_session_start_records_the_summary(tmp_path, home):
    repo = Repo(tmp_path / "repo")
    repo.prompt("rewrite the CSV exporter so it streams rows instead of loading them all")
    payload = dict(load_payload("claude-code", "SessionStart-compact"), session_id="s1",
                   transcript_path=str(transcript(home)), source="compact")
    repo.send("claude-code", payload)
    save = repo.save()
    assert save["summary"]["agent"] == "claude-code" and len(save["summary"]["text"]) <= 1200
    assert "## Session summary (Claude Code compacted its context)\n    This session is being continued" in repo.markdown()
    line = next(l for l in context(save, force=True).splitlines() if l.startswith("Session summary"))
    assert line.startswith('Session summary (Claude Code compacted its context): "This session is being continued')
    assert len(line) <= 400


def test_codex_compacted_item_with_an_empty_message_is_ignored():
    rows = rollout.tail_lines(FIXTURES / "codex/rollouts/rollout-real-session.jsonl")
    assert any(r.get("type") == "compacted" for r in rows)
    assert rollout.compacted(rows) is None  # message "" in the recording: never rebuilt from history


def test_codex_compacted_message_is_recorded_once(tmp_path):
    repo = Repo(tmp_path / "repo", agents=("codex",))
    path = tmp_path / "rollout-2026-10-17T12-00-00-y.jsonl"
    item = {"timestamp": "2026-10-17T13:20:00.000Z", "type": "compacted",
            "payload": {"message": "Streaming done; export_all() wrapper next.", "replacement_history": []}}
    path.write_text(json.dumps(item) + "\n")
    prompt = dict(codex_recording("plan-handoff/UserPromptSubmit-17be2b700d80.json"), transcript_path=str(path),
                  prompt="rewrite the CSV exporter so it streams rows instead of loading them all")
    repo.send("codex", prompt)
    stop = dict(codex_recording("plan-handoff/Stop-9f956362cdb2.json"), transcript_path=str(path))
    repo.send("codex", stop)
    repo.send("codex", stop)
    rows = [json.loads(l) for l in (repo.root / ".autosave/events.jsonl").read_text().splitlines()]
    assert sum(r["kind"] == "summary" for r in rows) == 1
    assert repo.save()["summary"]["text"] == "Streaming done; export_all() wrapper next."


def test_the_compaction_reader_fails_silently(home):
    """Whatever the transcript or path holds, the reader returns None and never raises."""
    path = home / ".claude/projects/-repo/odd.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    for name in ("/x\0y.jsonl", 42, b"/bytes.jsonl", str(path.parent)):
        assert claude.compact_summary(name) is None
    for body in (b"\xff\xfe isCompactSummary \x00\n", b'{"isCompactSummary": true}\n',
                 b'{"isCompactSummary": true, "message": {"content": [1, {"text": 2}, null]}}\n',
                 b'{"isCompactSummary": true, "x": ' + b"[" * 100000 + b"]" * 100000 + b"}\n",
                 b'["isCompactSummary"]\n'):
        path.write_bytes(body)
        assert claude.compact_summary(str(path)) is None
