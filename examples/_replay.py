"""Shared by every examples/<name>/replay.py: rebuild an example from its ledger.

From .autosave/events.jsonl alone this rebuilds .autosave/save.json,
.autosave/SAVE.md, before/SAVE.md (the save when the first agent stopped)
and context.txt (what the second agent was given at its SessionStart),
with the same compiler the hooks use. Needs agent-autosave importable:
installed, or run from a checkout of the repository.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "agent_autosave").is_dir():
    sys.path.insert(0, str(ROOT))


def pickup_index(events: list) -> int:
    """Index of the second agent's first event (CLI events belong to the first)."""
    first = events[0]["agent"]
    return next(i for i, e in enumerate(events) if e["agent"] not in (first, "cli"))


def rebuild(folder: Path, go: bool = False) -> dict:
    """{path relative to the example folder: bytes} for every file the ledger determines.
    `go` adds go.txt: what `agent-autosave go` printed when the first agent
    stopped, and the first prompt it started the second agent with."""
    from agent_autosave.compiler import compile_events, serialize
    from agent_autosave.loader import context
    from agent_autosave.render import render
    lines = (folder / ".autosave/events.jsonl").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines]
    started, task_id = events[0]["ts"], events[0]["task_id"]
    save = compile_events(events, started=started, task_id=task_id)
    pickup = pickup_index(events)
    before = compile_events(events[:pickup], started=started, task_id=task_id)
    start = events[pickup]
    now = datetime.strptime(start["ts"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    block = context(before, now=now, agent=start["agent"], session=start["session"])
    files = {".autosave/save.json": serialize(save), ".autosave/SAVE.md": render(save).encode("utf-8"),
             "before/SAVE.md": render(before).encode("utf-8"), "context.txt": (block + "\n").encode("utf-8")}
    if go:
        files["go.txt"] = go_text(before, events[0]["agent"], start["agent"], now).encode("utf-8")
    return files


def go_text(before: dict, first: str, second: str, now: datetime) -> str:
    """The switch prompt (without its local clock time, so the file is the
    same everywhere) and the resume block go hands the next agent."""
    from agent_autosave.commands.common import NAMES
    from agent_autosave.commands.go import prompt_text, should_switch
    from agent_autosave.config import DEFAULTS
    from agent_autosave.loader import context
    reason = should_switch(before)
    if reason is None:
        raise ValueError("the first agent did not stop on a limit")
    prompt = context(before, now=now, force=True, budget=DEFAULTS["inject"]["resume_max_chars"])
    return "\n".join(["$ agent-autosave go {} {}".format(first, second), prompt_text(first, second, None, reason), "",
                      "# {}'s first prompt:".format(NAMES.get(second, second)), prompt, ""])


def main(folder: Path, argv: list, go: bool = False) -> int:
    check = "--check" in argv
    stale = []
    for name, data in rebuild(folder, go).items():
        path = folder / name
        if check:
            if not path.is_file() or path.read_bytes() != data:
                stale.append(name)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        print("wrote " + name)
    if check:
        print("differs from the replay: " + ", ".join(stale) if stale else "up to date: the replay matches")
    return 1 if stale else 0
