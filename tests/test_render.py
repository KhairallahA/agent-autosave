from copy import deepcopy

import pytest

from agent_autosave.compiler import compile_events
from agent_autosave.render import REMINDER, render
from tests.helpers import event


def large_save():
    save = compile_events([event("prompt", text="Build the portable autosave compiler.")])
    save["last_actions"] = ["action-{} ".format(i) + "a" * 180 for i in range(10)]
    save["steering"] = ["steering-{} ".format(i) + "s" * 180 for i in range(5)]
    save["failing"] = {"command": "pytest", "exit": 1, "tail": "\n".join("tail-{} ".format(i) + "t" * 80 for i in range(20))}
    save["files_touched"] = ["file-{}-".format(i) + "f" * 40 for i in range(30)]
    return save


def test_ordered_reductions_stop_as_soon_as_budget_met(monkeypatch):
    import agent_autosave.render as renderer
    source = large_save()
    original = deepcopy(source)
    body = renderer._body
    for stop in range(1, 5):
        seen = []
        def observe(view):
            seen.append(deepcopy(view))
            return "x" * (13000 if len(seen) <= stop else 10)
        monkeypatch.setattr(renderer, "_body", observe)
        text = render(source)
        assert len(text) <= 12000
        assert len(seen) == stop + 1
        for stage, view in enumerate(seen):
            assert len(view["last_actions"]) == (5 if stage >= 1 else 10)
            assert len(view["steering"]) == (3 if stage >= 2 else 5)
            assert len(view["failing"]["tail"].splitlines()) == (10 if stage >= 3 else 20)
            assert len(view["files_touched"]) == (15 if stage >= 4 else 30)
    monkeypatch.setattr(renderer, "_body", body)
    assert source == original


@pytest.mark.parametrize("limit", [256, 1000, 12000, 16000])
def test_hard_cap_on_individually_oversized_fields_preserves_next_action_and_reminder(limit):
    save = large_save()
    save["decisions"] = ["🛠" * 20000]
    save["next_action"] = "Next action " + "x" * 17000
    text = render(save, {"compile": {"save_md_max_chars": limit}})
    assert len(text) <= min(16000, limit)
    assert "## Next action\nNext action" in text
    assert text.endswith(REMINDER)
    assert "Details truncated" in text
    assert text == render(save, {"compile": {"save_md_max_chars": limit}})


def test_untrusted_markdown_cannot_inject_headings_and_absent_plan_omitted():
    save = compile_events([event("prompt", text="A useful task\n# forged heading <script>")])
    text = render(save)
    assert "\n# forged" not in text
    assert "\\# forged heading \\<script\\>" in text
    assert "## Plan" not in text
