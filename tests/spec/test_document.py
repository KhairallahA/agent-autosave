"""docs/spec/save-format-v1.md stays in step with the code and the schemas."""
import json
import re
from pathlib import Path

from agent_autosave import journal, loader, memory, recall, render
from agent_autosave.commands import handoff
from agent_autosave.installer import install

ROOT = Path(__file__).resolve().parents[2]
SPEC = (ROOT / "docs/spec/save-format-v1.md").read_text(encoding="utf-8")


def test_every_schema_is_listed():
    for path in sorted((ROOT / "schemas").glob("*.schema.json")):
        assert "(../../schemas/{})".format(path.name) in SPEC, path.name


def test_every_event_kind_and_its_fields_are_in_the_table():
    common = {"v", "ts", "agent", "session", "turn", "kind", "task_id", "id"}
    for branch in json.loads((ROOT / "schemas/event.schema.json").read_text(encoding="utf-8"))["oneOf"]:
        props, required = branch["properties"], set(branch["required"])
        kind = props["kind"]["const"]
        row = re.search(r"^\| `{}` \| (.*) \| (.*) \|$".format(kind), SPEC, re.M)
        assert row, kind
        listed = [set(re.findall(r"`(\w+)`", cell)) for cell in row.groups()]
        assert listed == [{k for k in props if k in required} - common, {k for k in props if k not in required} - common], kind


def test_save_md_sections_and_markers_match_the_renderer():
    source = (ROOT / "agent_autosave/render.py").read_text(encoding="utf-8")
    for heading in set(re.findall(r'"## ([A-Z][^"\\]*)"', source)) | {"Before you continue", "Session summary"}:
        assert "`## {}".format(heading) in SPEC, heading
    assert "`<!-- agent-autosave v:1 -->`" in SPEC and render.REMINDER.startswith("## Before you continue")
    assert "[Details truncated to fit the save budget.]" in SPEC and "[Details truncated" in source


def test_block_markers_match_the_code():
    for marker in (loader.MARKER, recall.HEADER, recall.START_HEADER, handoff.EVENTS_MARKER,
                   install.MARKER_START, install.MARKER_END, install.HASH_START, install.HASH_END):
        assert "`{}`".format(marker) in SPEC, marker
    assert "`{}`".format(loader.ENDING.strip()) in SPEC


def test_memory_and_journal_sections_match_the_code():
    for heading in memory.HEADINGS.values():
        assert "`## {}`".format(heading) in SPEC
    for section in journal.SECTIONS:
        assert "`## {}`".format(section) in SPEC
    for key in json.loads((ROOT / "schemas/handoff.schema.json").read_text(encoding="utf-8"))["required"]:
        assert "`{}`".format(key) in SPEC


def test_the_version_policy_is_stated():
    policy = SPEC[SPEC.index("## Version policy"):]
    assert "version 1" in policy and "version 2" in policy and "migration" in policy
