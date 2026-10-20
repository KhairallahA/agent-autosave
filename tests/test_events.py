import json
from pathlib import Path

import pytest

from agent_autosave.config import resolve
from agent_autosave.events import EventError, prepare, validate
from tests.helpers import event, tool


VALID = [event("session_start", source="startup"), event("prompt", text="hello"), tool(),
         event("plan", items=[{"step": "ship", "state": "todo"}]), event("turn_end"),
         event("compact", trigger="auto"), event("interrupted", reason="rate_limit"),
         event("note", text="Use Python", type="decision"), event("session_end", reason="exit"),
         event("plan_set", steps=["a", "b"]), event("plan_done", index=2), event("task_done", note="Shipped")]


@pytest.mark.parametrize("value", VALID, ids=lambda x: x["kind"])
def test_all_kinds_and_schema_contract(value):
    validate(value)
    schema = json.loads((Path(__file__).parents[1] / "schemas/event.schema.json").read_text())
    branches = {item["properties"]["kind"]["const"]: item for item in schema["oneOf"]}
    from agent_autosave.events import EXTRA
    assert set(branches) == set(EXTRA)
    branch = branches[value["kind"]]
    assert set(branch["required"]) <= value.keys() <= branch["properties"].keys()
    assert branch["additionalProperties"] is False


@pytest.mark.parametrize("patch", [
    {"v": 2}, {"v": True}, {"turn": True}, {"turn": -1}, {"ts": "2026-13-14T00:00:00Z"},
    {"ts": "2026-10-14T00:00:00+03:00"}, {"kind": "future_kind"}, {"agent": ""},
    {"session": []}, {"files": "file.py"}, {"exit": False}, {"file_contents": "forbidden"},
    {"task_id": "../../outside"},
])
def test_reject_invalid_or_future_events(patch):
    with pytest.raises(EventError):
        validate(dict(tool(), **patch))


@pytest.mark.parametrize("value", [event("plan", items=None), event("plan", items=[{"step":"", "state":"todo"}]),
    event("plan", items=[{"step":"ship", "state": []}]), event("compact", trigger="wrong"),
    event("interrupted", reason="invented"), event("note", text="text", type="invalid"), {},
    event("plan_set", steps=[]), event("plan_set", steps=[" "]), event("plan_done", index=0),
    event("plan_done", index=True), event("plan", items=[], origin="cli")])
def test_reject_invalid_payloads(value):
    with pytest.raises(EventError):
        validate(value)


def test_capture_limits_and_discards_success_output():
    config = resolve()
    assert len(prepare(event("prompt", text="a" * 3000), config)["text"]) == 2000
    assert len(prepare(event("turn_end", last_message="a" * 2900), config)["last_message"]) == 2000
    assert "output_tail" not in prepare(tool(output_tail="do not keep success output"), config)
    failure = prepare(tool(exit=1, summary=" pytest  ", output_tail="\n".join(map(str, range(30)))), config)
    assert failure["summary"] == "pytest"
    assert failure["output_tail"].splitlines() == list(map(str, range(10, 30)))
