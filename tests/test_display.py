"""How commands and messages are shown in SAVE.md, the context and show:
short, without temp paths, cut at a word boundary. The ledger keeps them whole."""
import json

from agent_autosave.compiler import compile_events
from agent_autosave.loader import context
from agent_autosave.render import _text, render
from tests.helpers import event, tool

ROOT = "/private/tmp/aa-cont"
LONG = ("cd /private/claude-501 2>/dev/null; S=/private/tmp/claude-501/-private-tmp-aa-cont/"
        "0f6c2a9e-1111-4222-8333-944455556666/scratchpad; python3 " + ROOT + "/export.py --out " + ROOT + "/out.csv "
        "--rows 1000000 --chunk-size 50000 --compress gzip --verbose --log-file $S/export.log")


def shown(save):
    return save.get("last_actions_shown") or save["last_actions"]


def test_last_actions_drop_temp_paths_and_the_project_root():
    save = compile_events([event("prompt", 0, text="stream the CSV exporter"), tool(1, summary=LONG, exit=0)], root=ROOT)
    [action] = shown(save)
    assert "/private/tmp" not in action and "claude-501" not in action and "scratchpad" not in action
    assert "<tmp>" in action and "export.py" in action and ROOT not in action
    assert len(action) <= 100 + len(" (exit 0)") and "…" in action and action.endswith(" (exit 0)")
    body = action[len("Ran "):-len(" (exit 0)")]
    assert not body[:-1].endswith(("-", "/")) and body[-2] != " "  # cut at a word boundary
    assert save["last_actions"] == ["Ran " + LONG + " (exit 0)"]  # the save keeps it whole
    assert _text(action) in render(save) and json.dumps(action) in context(save, force=True)


def test_temp_path_forms():
    from agent_autosave.display import command
    assert command("cat /tmp/x/a.txt") == "cat <tmp>"
    assert command("ls /var/folders/zz/abc123/T/claude-501/s") == "ls <tmp>"
    assert command("ls /private/var/folders/zz/abc/T/x") == "ls <tmp>"
    assert command("cd /repo/src && ls /repo", root="/repo") == "cd src && ls ."
    assert command("ls /repository", root="/repo") == "ls /repository"
    assert command("a   b\n\tc") == "a b c"


def test_shorten_cuts_at_a_word_boundary():
    from agent_autosave.display import shorten
    assert shorten("short", 100) == "short"
    text = "word " * 40
    out = shorten(text, 100)
    assert len(out) <= 100 and out.endswith("word…")
    assert shorten("x" * 150, 100) == "x" * 99 + "…"


FINISHED = "Nothing: the task is finished."


def test_a_done_task_has_nothing_next():
    save = compile_events([event("prompt", 0, text="add OAuth login to the settings page"), tool(1, summary="pytest -q", exit=0),
                           dict(event("task_done", 2), agent="cli", session="cli")])
    assert save["status"] == "done" and save["next_action"] == FINISHED
    assert "## Next action\n" + FINISHED in render(save)
    assert "Suggested next step: " + json.dumps(FINISHED) + "." in context(save, force=True)


def test_an_empty_slot_is_never_stale():
    from agent_autosave.loader import stale_reason
    empty = compile_events([], {"head": "aaaaaaaaaaaa", "branch": "feature", "dirty": []}, started="2026-10-14T12:00:00Z")
    assert stale_reason(empty, current_head="bbbbbbbbbbbb", commits_since=1) == ""
    worked = compile_events([event("prompt", 0, text="add OAuth login to the settings page")], {"head": "aaaaaaaaaaaa", "branch": "f", "dirty": []})
    assert stale_reason(worked, current_head="bbbbbbbbbbbb", commits_since=1) == "This save may be stale: 1 commit landed since."
