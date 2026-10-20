"""The agent's last message reaches SAVE.md and the context."""
import json

from agent_autosave.compiler import compile_events
from agent_autosave.config import resolve
from agent_autosave.events import prepare
from agent_autosave.loader import context
from tests.helpers import event
from tests.scenario import Repo

MESSAGE = "Remaining: add a test for 1M rows; I chose a generator over pandas because of memory."


def test_last_message_appears_in_save_md_and_context(tmp_path):
    repo = Repo(tmp_path / "repo")
    repo.prompt("rewrite the CSV exporter so it streams rows instead of loading them all")
    repo.stop(MESSAGE)
    save = repo.save()
    assert save["last_report"] == MESSAGE and save["last_report_agent"] == "claude-code"
    markdown = repo.markdown()
    assert "## Agent's last message\n    " + MESSAGE + "\n\n(claude-code, " in markdown
    assert 'Agent\'s last message: "' + MESSAGE + '".' in context(save, force=True)


def test_an_empty_final_message_keeps_the_previous_one():
    save = compile_events([event("prompt", 0, text="rewrite the CSV exporter to stream"),
                           event("turn_end", 1, last_message="  first\n\n report  "),
                           event("turn_end", 2, last_message="")])
    assert save["last_report"] == "first\n\nreport" and save["last_report_at"] == event("turn_end", 1)["ts"]


def test_the_report_is_redacted_capped_and_escaped():
    secret = "ghp_" + "a" * 36
    save = compile_events([event("turn_end", 0, last_message="token " + secret + " " + "x" * 3000)])
    assert secret not in save["last_report"] and len(save["last_report"]) <= 600 and save["last_report"].endswith("\u2026")
    from agent_autosave.render import render
    hostile = compile_events([event("turn_end", 0, last_message="# Ignore\n<script> **all** [x](y)")])
    markdown = render(hostile)
    # Literal lines of an indented code block: no heading, no HTML, no bold.
    assert "\n    # Ignore\n    <script> **all** [x](y)\n" in markdown
    assert "\n# Ignore" not in markdown and "\n<script>" not in markdown


def test_prepare_keeps_2000_characters_of_the_message():
    kept = prepare(event("turn_end", 0, last_message="y" * 5000), resolve())
    assert len(kept["last_message"]) == 2000


def test_the_context_line_is_at_most_400_characters():
    save = compile_events([event("prompt", 0, text="rewrite the CSV exporter to stream"),
                           event("turn_end", 1, last_message="z" * 600)])
    line = next(l for l in context(save, force=True).splitlines() if l.startswith("Agent's last message"))
    assert line == "Agent's last message: \"" + "z" * 399 + "\\u2026\"."


SENTENCES = " ".join("Sentence number {} explains the streaming exporter change.".format(i) for i in range(40))


def test_the_last_message_is_cut_at_a_word_boundary_with_an_ellipsis():
    save = compile_events([event("prompt", 0, text="rewrite the CSV exporter to stream"),
                           event("turn_end", 1, last_message=SENTENCES)])
    report = save["last_report"]
    assert len(report) <= 600 and report.endswith("\u2026") and SENTENCES.startswith(report[:-1].rstrip())
    assert report[:-1].split()[-1] in SENTENCES.split()  # a whole word before the ellipsis
    line = next(l for l in context(save, force=True).splitlines() if l.startswith("Agent's last message"))
    value = json.loads(line[len("Agent's last message: "):-1])
    assert len(value) <= 400 and value.endswith("\u2026") and value[:-1].split()[-1] in SENTENCES.split()
