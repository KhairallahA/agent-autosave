"""Every golden and example save and ledger line matches its JSON schema."""
import json
from pathlib import Path

import pytest

from tests.schema import errors

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures"
SAVE_SCHEMA = json.loads((ROOT / "schemas/save.schema.json").read_text(encoding="utf-8"))
EVENT_SCHEMA = json.loads((ROOT / "schemas/event.schema.json").read_text(encoding="utf-8"))
SAVES = sorted(FIXTURES.glob("**/save.json")) + sorted(ROOT.glob("examples/*/.autosave/save.json"))
LEDGERS = (sorted(FIXTURES.glob("compiler/*/events.jsonl")) + sorted(FIXTURES.glob("codex/**/*.jsonl"))
           + sorted(ROOT.glob("examples/*/.autosave/events.jsonl")))
LEDGERS = [p for p in LEDGERS if "rollouts" not in p.parts]  # rollouts are Codex's format, not ours


def name(path):
    return str(path.relative_to(ROOT))


def test_the_inputs_are_found():
    assert len(SAVES) >= 28 and any("examples" in p.parts for p in SAVES)
    assert len(LEDGERS) >= 28 and any("examples" in p.parts for p in LEDGERS)


@pytest.mark.parametrize("path", SAVES, ids=name)
def test_save_matches_the_save_schema(path):
    assert errors(json.loads(path.read_text(encoding="utf-8")), SAVE_SCHEMA) == []


@pytest.mark.parametrize("path", LEDGERS, ids=name)
def test_every_ledger_line_matches_the_event_schema(path):
    problems = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            problems += ["line {}: {}".format(number, e) for e in errors(json.loads(line), EVENT_SCHEMA)]
    assert problems == []


def test_the_validator_catches_what_the_schemas_rule_out():
    save = json.loads(SAVES[0].read_text(encoding="utf-8"))
    assert errors(dict(save, status="sleeping"), SAVE_SCHEMA)
    assert errors(dict(save, extra=1), SAVE_SCHEMA)
    assert errors({k: v for k, v in save.items() if k != "goal"}, SAVE_SCHEMA)
    assert errors({"v": 1, "ts": "yesterday", "agent": "codex", "session": "s", "turn": 0,
                   "kind": "prompt", "text": "hi"}, EVENT_SCHEMA)
    with pytest.raises(ValueError):
        errors(1, {"maxLength": 3})
