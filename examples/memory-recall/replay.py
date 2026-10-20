"""Rebuild this example from .autosave/events.jsonl and prompt.txt.

From Codex's ledger this compiles the save, writes its journal page and the
MEMORY.md you get by accepting Codex's decision note, then runs recall as
the hooks do: start.txt is what a later Claude Code session starts with,
recall.txt what its prompt (prompt.txt) is given. `--check` exits 1 if a
committed file differs. Needs agent-autosave importable: installed, or run
from a checkout of the repository. Dates are in UTC here, so the example is
the same in every time zone; a real sync uses your local date.
"""
from __future__ import annotations

from datetime import date, timezone
import json
from pathlib import Path
import shutil
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if (ROOT / "agent_autosave").is_dir():
    sys.path.insert(0, str(ROOT))


def rebuild(folder: Path) -> dict:
    """{path relative to the example folder: bytes} for every derived file."""
    from agent_autosave import config as config_module, journal, memory, recall
    from agent_autosave.compiler import compile_events, serialize
    from agent_autosave.loader import AGENT_NAMES
    from agent_autosave.memory_sync import rebuild_index
    events = [json.loads(line) for line in (folder / ".autosave/events.jsonl").read_text(encoding="utf-8").splitlines()]
    save = compile_events(events, started=events[0]["ts"], task_id=events[0]["task_id"])
    accepted = date.fromisoformat(events[-1]["ts"][:10])  # the user accepted it when the task was done
    document = memory.Document.parse(memory.TEMPLATE)
    entries = [document.add(note["type"], note["text"], source={"agent": note["agent"], "task_id": save["task_id"]},
                            today=accepted) for note in save["memory_notes"]]
    page = journal.relative_path(save, tz=timezone.utc)
    files = {".autosave/save.json": serialize(save),
             ".autosave/MEMORY.md": document.render().encode("utf-8"),
             ".autosave/" + page: journal.render_page(save, [e.id for e in entries], AGENT_NAMES, tz=timezone.utc).encode("utf-8")}
    prompt = (folder / "prompt.txt").read_text(encoding="utf-8").strip()
    later = date.fromisoformat("2026-10-16")
    with tempfile.TemporaryDirectory() as scratch:
        project = Path(scratch)
        for name, data in files.items():
            (project / name).parent.mkdir(parents=True, exist_ok=True)
            (project / name).write_bytes(data)
        config = config_module.load(project, user_path=project / "no-user-config.json")
        rebuild_index(project, config, later, full=True)
        start = recall.start_block(project, config, recall.session_memory(project, config, None, later))
        block, _ = recall.recall(project, config, prompt, agent="claude-code", today=later)
        shutil.rmtree(str(project / ".autosave/memory"))
    files["start.txt"] = (start + "\n").encode("utf-8")
    files["recall.txt"] = (block + "\n").encode("utf-8")
    return files


def main(argv: list) -> int:
    check = "--check" in argv
    stale = []
    for name, data in rebuild(HERE).items():
        path = HERE / name
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


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
