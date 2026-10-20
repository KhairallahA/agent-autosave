"""The next step after a turn that did work, and the agent's last message
rendered as a block that keeps its line breaks."""
import json

from agent_autosave.compiler import compile_events
from agent_autosave.loader import context
from agent_autosave.render import render
from tests.helpers import event, tool

GOAL = "rewrite the CSV exporter so it streams rows instead of loading them all"
ASK = "Read the agent's last message, check git, and ask the user what is left."


def test_with_actions_and_a_last_message_the_next_step_asks_what_is_left():
    save = compile_events([event("prompt", 0, text=GOAL), tool(1), event("turn_end", 2, last_message="Done the exporter.")])
    assert save["next_action"] == ASK


def test_the_goal_stays_the_next_step_only_when_nothing_was_done():
    save = compile_events([event("prompt", 0, text=GOAL)])
    assert save["next_action"] == GOAL
    save = compile_events([event("prompt", 0, text=GOAL), event("turn_end", 1, last_message="Which file?")])
    assert save["next_action"] == GOAL


def test_a_plan_or_a_failure_still_wins():
    failing = compile_events([event("prompt", 0, text=GOAL), tool(1, exit=1), event("turn_end", 2, last_message="It fails.")])
    assert failing["next_action"] == "Make `pytest` pass."
    planned = compile_events([event("prompt", 0, text=GOAL), tool(1),
                              event("plan_set", 2, steps=["write tests"]), event("turn_end", 3, last_message="x")])
    assert planned["next_action"] == "write tests"


REPORT = "Done:\n- **export.py** streams rows\n```\npytest -q\n```\n# Next\nadd a 1M-row test"


def test_the_last_message_is_an_indented_block_with_its_line_breaks():
    markdown = render(compile_events([event("prompt", 0, text=GOAL), event("turn_end", 1, last_message=REPORT)]))
    block = markdown.split("## Agent's last message\n", 1)[1].split("\n\n", 1)[0].splitlines()
    assert all(line.startswith("    ") for line in block) and len(block) >= 6
    assert "    Done:" in block and "    # Next" in block
    # No line of the block can become a heading, fence or list in SAVE.md.
    headings = [line for line in markdown.splitlines() if line.startswith("#")]
    assert "# Next" not in headings and not any(line.startswith("```") for line in markdown.splitlines())


def test_the_context_line_strips_markdown_markers_before_quoting():
    save = compile_events([event("prompt", 0, text=GOAL), event("turn_end", 1, last_message=REPORT)])
    line = next(l for l in context(save, force=True).splitlines() if l.startswith("Agent's last message"))
    value = json.loads(line[line.index('"'):-1])
    assert "```" not in value and "**" not in value and "- " not in value.split("Done:")[1][:3]
    assert "export.py streams rows" in value and len(value) <= 400
