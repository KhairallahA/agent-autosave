"""Stuck: the same command failing three or more times in a row."""
from agent_autosave.compiler import compile_events
from agent_autosave.loader import context
from tests.helpers import event, tool
from tests.scenario import Repo


def edit(n):
    return event("tool", n, tool="Edit", summary="Edited a.py", files=["a.py"])


def test_three_failures_in_a_row_are_stuck_with_the_edits_between():
    events = [tool(0, summary="pytest tests/billing", exit=1), edit(1), tool(2, summary="pytest tests/billing", exit=1),
              edit(3), edit(4), tool(5, summary="ls", exit=0), tool(6, summary="pytest tests/billing", exit=1),
              edit(7), tool(8, summary="pytest tests/billing", exit=1)]
    stuck = compile_events(events)["stuck"]
    assert stuck == {"command": "pytest tests/billing", "failures": 4, "since": events[0]["ts"], "edits_between": 4}


def test_two_failures_are_not_stuck_and_a_pass_clears_it():
    two = [tool(0, summary="pytest", exit=1), tool(1, summary="pytest", exit=1)]
    assert compile_events(two)["stuck"] is None
    cleared = two + [tool(2, summary="pytest", exit=1), tool(3, summary="pytest", exit=0)]
    assert compile_events(cleared)["stuck"] is None
    again = cleared + [tool(4 + i, summary="pytest", exit=1) for i in range(3)]
    assert compile_events(again)["stuck"]["failures"] == 3 and compile_events(again)["stuck"]["since"] == again[4]["ts"]


def test_an_unknown_exit_neither_counts_nor_clears():
    events = [tool(0, summary="pytest", exit=1), tool(1, summary="pytest", exit=None), tool(2, summary="pytest", exit=1),
              tool(3, summary="pytest", exit=1)]
    assert compile_events(events)["stuck"]["failures"] == 3


def test_the_newest_stuck_command_is_shown():
    events = [tool(i, summary="a", exit=1) for i in range(3)] + [tool(3 + i, summary="b", exit=2) for i in range(3)]
    assert compile_events(events)["stuck"]["command"] == "b"


def test_end_to_end_stuck_in_context_and_under_failing(tmp_path):
    repo = Repo(tmp_path / "repo")
    repo.prompt("fix the billing export so the nightly job passes again")
    for _ in range(4):
        repo.bash("pytest tests/billing", exit=1)
        repo.claude("PostToolUse-Edit")
    repo.bash("pytest tests/billing", exit=1)
    save = repo.save()
    since = save["stuck"]["since"][11:16]
    line = ('Stuck: "pytest tests/billing" failed 5 times since {} UTC with 4 edits tried. '
            "Try a different approach before re-running it.").format(since)
    assert line in context(save, force=True)
    markdown = repo.markdown()
    failing = markdown.index("## Failing")
    assert markdown.index("Stuck: `pytest tests/billing` failed 5 times since ", failing) < markdown.index("## Files and git")
