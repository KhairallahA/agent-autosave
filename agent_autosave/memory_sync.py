"""All memory work that must stay off a hook's hot path, in one place.

`sync(project)` runs in the background process (session end, a turn end in
vault or agentic-stack mode, after a hook archived a task) and directly in
commands that need fresh pages (clear, done, journal, memory, vault move):

1. a journal page for each archived save and for a done current save;
2. memory candidates from those saves' decision, tried and warning notes;
3. the recall index;
4. vault: CURRENT.md and the home note;
5. agentic-stack: the WORKSPACE.md block, a case per task archived as done.

Each step logs its own error to .autosave/errors.log; the next one still runs.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

HISTORY_MAX_BYTES = 4 * 1024 * 1024


def _state_path(project: Path) -> Path:
    return project / ".autosave" / "memory" / "state.json"


def read_state(project: str | Path) -> dict[str, Any]:
    from .storage import read_view
    state = read_view(_state_path(Path(project)))
    if not isinstance(state, dict) or not isinstance(state.get("pages"), dict):
        state = {"v": 1, "pages": {}}
    return state


def archived_saves(project: Path) -> list[dict[str, Any]]:
    """Archived saves (history/), then the current save when it is done."""
    from .storage import read_json, read_view
    out = []
    history = project / ".autosave" / "history"
    for path in sorted(history.glob("t_*.json")) if history.is_dir() else []:
        try:
            if path.is_symlink() or path.stat().st_size > HISTORY_MAX_BYTES:
                continue
            save = read_json(path)
        except (OSError, ValueError):
            continue
        if isinstance(save, dict) and isinstance(save.get("task_id"), str) and isinstance(save.get("started"), str):
            out.append(save)
    current = read_view(project / ".autosave" / "save.json")
    if isinstance(current, dict) and current.get("status") == "done" and \
            current.get("task_id") not in {s["task_id"] for s in out}:
        out.append(current)
    return out


def _digest(save: dict[str, Any], memory_ids: list[str]) -> str:
    payload = json.dumps({"save": save, "memory": memory_ids}, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def local_zone() -> Any:
    """The user's zone for human-facing dates (journal names, `date`, lists,
    recall); passed into the pure page and index builders."""
    return datetime.now().astimezone().tzinfo


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def sync(project: str | Path, *, today: date | None = None, run_case: Callable[..., None] | None = None) -> dict[str, Any]:
    """Bring journal, candidates, index, vault and agentic-stack up to date.
    Returns what changed: {"pages": [...], "candidates": [...], "cases": [...]}."""
    from .config import load
    from .storage import log_error
    project = Path(project)
    config = load(project)
    today = today or date.today()
    report: dict[str, Any] = {"pages": [], "candidates": [], "cases": []}
    from . import memory
    with memory.lock(project):
        state = read_state(project)
        saves = archived_saves(project)
        steps = [("journal", lambda: _journal(project, config, saves, state, report, today)),
                 ("candidates", lambda: _candidates(project, config, saves, state, report)),
                 ("index", lambda: rebuild_index(project, config, today, changed=report["pages"])),
                 ("vault", lambda: _vault(project, config)),
                 ("agentic_stack", lambda: _agentic(project, config, saves, state, report, run_case))]
        for name, step in steps:
            try:
                step()
            except Exception as error:  # one failing step must not stop the others
                log_error(project / ".autosave", "memory.sync." + name, error)
        from .storage import atomic_write
        atomic_write(_state_path(project), state, durable=False)
    return report


def _journal(project, config, saves, state, report, today):
    from . import journal, memory, places
    from .loader import AGENT_NAMES
    from .redact import Redactor
    document = memory.read(project, config)
    by_task: dict[str, list[str]] = {}
    for entry in document.entries():
        task = ((entry.meta or {}).get("source") or {}).get("task_id")
        if task and entry.id and entry.active(today):
            by_task.setdefault(task, []).append(entry.id)
    redactor = Redactor.from_project(project, config)
    base = places.home(project, config, log=True)
    zone = local_zone()
    for save in saves:
        ids = by_task.get(save["task_id"], [])
        known = state["pages"].get(save["task_id"]) or {}
        # A page keeps the name it was first given (links point at it); new
        # pages are named by the local date.
        relative = known.get("path") or journal.relative_path(
            save, (page.get("path") for task, page in state["pages"].items() if task != save["task_id"]),
            tz=zone)
        digest = _digest(save, ids)
        path = base / relative
        if known.get("digest") == digest and known.get("path") == relative and path.is_file():
            continue
        if path.is_file() and journal.is_locked(path):
            state["pages"][save["task_id"]] = dict(known, path=relative, digest=digest)
            continue
        places.write(project, config, relative, journal.render_page(save, ids, AGENT_NAMES, tz=zone), redactor=redactor)
        state["pages"][save["task_id"]] = dict(known, path=relative, digest=digest)
        report["pages"].append(relative)


def _candidates(project, config, saves, state, report):
    from . import memory
    from .redact import Redactor
    redactor = Redactor.from_project(project, config)
    document = memory.read(project, config)
    rows = memory.candidates(project)
    before = len(rows)
    for save in saves:
        known = state["pages"].setdefault(save["task_id"], {})
        notes = save.get("memory_notes") or []
        offered = int(known.get("notes_offered") or 0)
        for note in notes[offered:]:
            row = memory.propose(project, config, memory.NOTE_TYPES[note["type"]], note["text"],
                                 source={"agent": note.get("agent") or "you", "task_id": save["task_id"]},
                                 created=note.get("at") or _now(), document=document, rows=rows, redactor=redactor)
            if row:
                report["candidates"].append(row["id"])
        known["notes_offered"] = len(notes)
    if len(rows) != before:
        memory.save_candidates(project, rows, redactor)


def rebuild_index(project: Path, config: dict[str, Any], today: date, *, changed: list[str] = (),
                  full: bool = False) -> None:
    """Memory and lesson docs from their files; journal docs updated for the
    pages just written, or read from every page when the index is missing."""
    from . import agentic, memory, places, recall
    data = recall.read_index(project)
    base = places.home(project, config)
    if data is None or full:
        journal = sorted(p.relative_to(base).as_posix() for p in (base / "journal").rglob("*.md")) \
            if (base / "journal").is_dir() else []
        docs = {}
    else:
        journal = list(changed)
        docs = {d["id"]: d for d in data["docs"] if d.get("kind") == "journal"}
    zone = local_zone()
    for relative in journal:
        try:
            docs[relative] = recall.journal_doc(relative, (base / relative).read_text(encoding="utf-8"), zone)
        except OSError:
            docs.pop(relative, None)
    lessons = agentic.lessons_file(project, config)
    memory_path = places.memory_file(project, config)
    index = {"v": 1, "memory": recall.stamp(memory_path), "lessons": recall.stamp(lessons),
             "docs": recall.memory_docs(memory.read(project, config).entries(), today)
             + sorted(docs.values(), key=lambda d: d["id"])
             + (recall.lesson_docs(lessons, zone) if lessons else [])}
    recall.write_index(project, index)


def _vault(project, config):
    from . import places, vault
    if not vault.configured(config) or not places.in_vault(project, config):
        return
    vault.ensure(project, config)


def _agentic(project, config, saves, state, report, run_case):
    from . import agentic
    from .storage import read_view
    if not agentic.enabled(project, config):
        return
    current = read_view(project / ".autosave" / "save.json")
    if isinstance(current, dict):
        agentic.write_workspace(project, current)
    run_case = run_case or agentic.add_case
    if not state.get("agentic_since"):
        # Turned on now: tasks archived before are not sent as cases.
        state["agentic_since"] = _now()
        for save in saves:
            state["pages"].setdefault(save["task_id"], {})["case"] = True
        return
    for save in saves:
        known = state["pages"].setdefault(save["task_id"], {})
        if save.get("status") != "done" or known.get("case"):
            continue
        known["case"] = True  # tried once: a failure is logged, never retried every turn
        run_case(project, save)
        report["cases"].append(save["task_id"])
