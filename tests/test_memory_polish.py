"""Polish of stored free text before memory pages share it: the session
summary without Claude Code's fixed preamble, rendered with its line breaks,
and the home directory shown as ~ in every stored free text."""
import json
import shutil

import pytest

from agent_autosave.compiler import compile_events
from agent_autosave.config import resolve
from agent_autosave.events import prepare
from agent_autosave.render import render
from agent_autosave.transcripts import claude
from agent_autosave.transcripts import codex as rollout
from tests.helpers import FIXTURES, event

RECORDED = FIXTURES / "claude-code/transcripts/compact-2.1.302.jsonl"
BOUNDARY, SUMMARY = [json.loads(line) for line in RECORDED.read_text().splitlines()]
# Claude Code's fixed opening, from a real 2.1.302 transcript.
PREAMBLE = ("This session is being continued from a previous conversation that ran out of context. "
            "The summary below covers the earlier portion of the conversation.\n\nSummary:\n")


def write(home, content):
    path = home / ".claude/projects/-tmp-x/s.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    summary = dict(SUMMARY, message={"role": "user", "content": content})
    path.write_text(json.dumps(BOUNDARY) + "\n" + json.dumps(summary) + "\n")
    return path


def test_the_fixed_preamble_is_dropped(home):
    found = claude.compact_summary(write(home, PREAMBLE + "1. Primary Request:\n   Write export.py"))
    assert found["text"] == "1. Primary Request:\n   Write export.py"


def test_a_summary_without_the_preamble_is_kept_whole(home):
    assert claude.compact_summary(write(home, "Summary: the user wants X"))["text"] == "Summary: the user wants X"


def test_the_preamble_is_dropped_from_list_content(home):
    content = [{"type": "text", "text": PREAMBLE + "Real summary"}]
    assert claude.compact_summary(write(home, content))["text"] == "Real summary"


def summary_save(text):
    events = [event("prompt", 0, text="Write export.py"),
              event("summary", 1, text=text, at="2026-10-14T12:00:01Z", trigger="manual",
                    pre_tokens=39427, post_tokens=3362)]
    return compile_events(events, None, resolve())


def test_save_md_shows_the_summary_as_an_indented_block_with_line_breaks():
    markdown = render(summary_save("1. Primary Request:\n   Write export.py\n# not a heading"))
    lines = markdown.splitlines()
    start = lines.index("## Session summary (Claude Code compacted its context: 39.4k to 3.4k tokens)")
    assert lines[start + 1:start + 4] == ["    1. Primary Request:", "       Write export.py", "    # not a heading"]
    assert "> " not in "\n".join(lines[start:start + 6])


@pytest.mark.parametrize("kind,field", [("note", "text"), ("turn_end", "last_message"), ("summary", "text")])
def test_home_directory_becomes_tilde_in_stored_free_text(home, kind, field):
    payload = {field: "Edited " + str(home) + "/Desktop/Projects/app/export.py"}
    if kind == "note":
        payload["type"] = "decision"
    clean = prepare(event(kind, 0, **payload), resolve())
    assert clean[field] == "Edited ~/Desktop/Projects/app/export.py"


def test_the_zstd_timeout_is_a_parameter_so_tests_never_race_the_clock(tmp_path):
    if not shutil.which("zstd"):
        pytest.skip("needs the zstd binary")
    import subprocess
    source = FIXTURES / "codex/rollouts/rollout-2026-10-15T10-00-00-normal.jsonl"
    target = tmp_path / "rollout-cold.jsonl.zst"
    subprocess.run(["zstd", "-q", "-o", str(target), str(source)], check=True)
    assert rollout.exit_code(rollout.tail_lines(target, zstd_timeout=60), "call_fail") == 7
    assert rollout.ZSTD_TIMEOUT == 0.15  # the hook keeps its 150 ms limit
