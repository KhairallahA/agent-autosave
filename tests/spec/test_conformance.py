"""Real files against docs/spec/save-format-v1.md and the schemas: a project
taken through every path that writes a file, the examples, and files from
an earlier release."""
import json
import re
import subprocess
from pathlib import Path

import pytest

from agent_autosave import journal, memory
from agent_autosave.commands import handoff
from tests.memory_helpers import accept_all, finish_task, repo  # noqa: F401  (fixture)
from tests.schema import errors

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = {path.name.split(".")[0]: json.loads(path.read_text(encoding="utf-8"))
           for path in (ROOT / "schemas").glob("*.schema.json")}
HEADINGS = ["## Plan", "## Where it stopped", "## Failing", "## Checks", "## Files and git", "## Agent's last message",
            "## Session summary", "## Decisions", "## Notes", "## Next action", "## Before you continue"]


def valid(value, name):
    found = errors(value, SCHEMAS[name])
    assert not found, found
    return True


def check_folder(directory: Path) -> set:
    """Validate everything we know in an .autosave/ folder; returns what was seen."""
    seen = set()
    for path in [directory / "events.jsonl"] + sorted((directory / "history").glob("events-*.jsonl")):
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                valid(json.loads(line), "event")
                seen.add("event")
    for path in [directory / "save.json"] + sorted((directory / "history").glob("t_*.json")) + \
            sorted((directory / "slots").glob("*.json")):
        if path.exists():
            seen.add("save") if valid(json.loads(path.read_text(encoding="utf-8")), "save") else None
    if (directory / "task.json").exists():
        seen.add("task") if valid(json.loads((directory / "task.json").read_text(encoding="utf-8")), "task") else None
    if (directory / "SAVE.md").exists():
        check_save_md((directory / "SAVE.md").read_text(encoding="utf-8"))
        seen.add("SAVE.md")
    candidates = directory / "memory/candidates.jsonl"
    if candidates.exists():
        for line in candidates.read_text(encoding="utf-8").splitlines():
            seen.add("candidate") if valid(json.loads(line), "candidate") else None
    if (directory / "MEMORY.md").exists():
        for entry in memory.Document.parse((directory / "MEMORY.md").read_text(encoding="utf-8")).entries():
            seen.add("memory-entry") if valid(entry.meta, "memory-entry") else None
    for page in sorted((directory / "journal").rglob("*.md")):
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}-[a-z0-9-]*-\d{6}(-\d+)?\.md", page.name), page.name
        seen.add("journal") if valid(journal.read_frontmatter(page.read_text(encoding="utf-8")), "journal") else None
    return seen


def check_save_md(text):
    assert text.startswith("<!-- agent-autosave v:1 -->\n# agent-autosave — ")
    found = [h for h in re.findall(r"^## .*$", text, re.M)]
    order = [next(i for i, h in enumerate(HEADINGS) if line.startswith(h)) for line in found]
    assert order == sorted(order) and len(order) == len(set(order)), found
    for required in ("## Where it stopped", "## Failing", "## Files and git", "## Decisions", "## Next action"):
        assert required in found


def test_every_file_a_project_writes_conforms(repo, monkeypatch):  # noqa: F811
    finish_task(repo)
    accept_all(repo)
    repo.prompt("Add pagination to the billing export endpoint", session="s2")
    repo.cli("plan", "set", "Add the page cursor", "Return the next link")
    repo.bash("pytest -q tests/test_export.py", exit=1, session="s2")
    subprocess.run(["git", "-C", str(repo.root), "switch", "-q", "-c", "feature/next"], check=True)
    repo.prompt("Write the release notes for the export work", session="s3")
    monkeypatch.chdir(repo.root)
    assert handoff.export(repo.root)[0]["ok"]
    seen = check_folder(repo.root / ".autosave")
    assert seen == {"event", "save", "task", "SAVE.md", "candidate", "memory-entry", "journal"}
    task = json.loads((repo.root / ".autosave/task.json").read_text(encoding="utf-8"))
    assert set(task["slots"]) == {"main", "feature/next"}
    meta, events = handoff.parse((repo.root / handoff.FILE).read_text(encoding="utf-8"))
    assert valid(meta, "handoff") and events and all(valid(e, "event") for e in events)


@pytest.mark.parametrize("folder", sorted(p.parent for p in (ROOT / "examples").glob("*/.autosave/events.jsonl")),
                         ids=lambda p: p.parent.name)
def test_examples_conform(folder):
    assert {"event", "save"} <= check_folder(folder)


def test_files_from_an_earlier_release_conform():
    folder = ROOT / "tests/fixtures/compat/main-d672782"
    assert {"event", "save", "SAVE.md"} <= check_folder(folder)
    old = json.loads((folder / "task.json").read_text(encoding="utf-8"))
    assert "slots" not in old  # the shape from before branch slots, still valid


def test_compiler_goldens_conform():
    goldens = sorted((ROOT / "tests/fixtures/compiler").rglob("*.json"))
    saves = [json.loads(p.read_text(encoding="utf-8")) for p in goldens]
    saves = [s for s in saves if isinstance(s, dict) and "task_id" in s and "last_actions" in s]
    assert saves and all(valid(s, "save") for s in saves)
