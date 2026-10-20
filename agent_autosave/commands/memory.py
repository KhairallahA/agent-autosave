"""`agent-autosave memory`: the project memory in MEMORY.md and its candidates.

Agents propose (notes, `memory add` without a terminal); only a person
accepts: `memory add` in an interactive terminal, `memory review`, or
`memory accept <id>`.
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
import re
import sys
from typing import Any

from ..contract import ExitCode, Outcome, fail, success
from . import common


def interactive() -> bool:
    """A person at a terminal: stdin and stdout are both TTYs."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def run(args: argparse.Namespace) -> Outcome:
    from .. import memory, memory_sync
    project = common.find_project()
    config = common.store(project).config
    if args.memory_action in ("list", "review"):
        memory_sync.sync(project)  # candidates from tasks archived since the last sync
    try:
        return ACTIONS[args.memory_action](project, config, args)
    except memory.UnknownId as error:
        return fail("unknown_entry", str(error), "Run agent-autosave memory list or agent-autosave memory review.")
    except ValueError as error:
        return fail("invalid_args", str(error), "Run agent-autosave memory --help.")


def _list(project, config, args) -> Outcome:
    from .. import memory, places
    from ..ui import style
    today = date.today()
    entries = [e.as_dict() for e in memory.read(project, config).entries()
               if (args.all or e.active(today)) and (not args.type or e.type == args.type)]
    data = {"entries": entries, "pending": len(memory.pending(project)),
            "file": places.shown(project, places.memory_file(project, config))}
    return success(data), ExitCode.SUCCESS, render_list(data, style())


def render_list(data: dict[str, Any], style: Any) -> str:
    lines = [style.heading("agent-autosave memory", data["file"]), ""]
    if data["entries"]:
        rows = [[style.dim("ID"), style.dim("TYPE"), style.dim("ADDED"), style.dim("FROM"), style.dim("TEXT")]]
        for e in data["entries"]:
            kind = e["type"] + (" (pinned)" if e["pinned"] else "") + (" (expired)" if e["section"] == "Expired" else "")
            rows.append([e["id"] or "-", kind, e["added"] or "-", (e["source"] or {}).get("agent") or "you",
                         e["text"][:70]])
        lines += style.table(rows)
    else:
        lines.append("  No memory entries yet. " + style.dim("Agents propose them with notes; accept them with ")
                     + style.command("agent-autosave memory review") + style.dim("."))
    if data["pending"]:
        lines += ["", style.item("warn", "{} candidate{} waiting: run agent-autosave memory review".format(
            data["pending"], "" if data["pending"] == 1 else "s"))]
    return "\n".join(lines)


def _date(value: str | None) -> str | None:
    """--until as YYYY-MM-DD, or None."""
    if value is None:
        return None
    message = "--until must be a date like 2026-12-31."
    if not re.fullmatch(r"\d{4}-\d\d-\d\d", value):
        raise ValueError(message)
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError(message) from None


def _add(project, config, args) -> Outcome:
    from .. import memory
    until = _date(args.until)
    if interactive():
        entry = memory.add_entry(project, config, args.type, args.text, source={"agent": "you", "task_id": None},
                                 today=date.today(), until=until, replaces=args.replaces)
        return success({"accepted": True, "entry": entry}), ExitCode.SUCCESS, \
            "Added to project memory: {} ({}).".format(entry["id"], entry["type"])
    replaces = memory.read(project, config).find(args.replaces).id if args.replaces else None
    return propose(project, config, args.type, args.text, until=until, replaces=replaces)


SEARCH_LIMIT = 10


def _search(project, config, args) -> Outcome:
    """Every memory entry, lesson and journal page matching a word of the
    query, best first: the recall scoring, without its threshold."""
    from .. import places, recall
    query = recall.terms(args.query)
    found = []
    for doc in recall.current(project, config, date.today())["docs"]:
        value = recall.score(query, doc)
        if value > 0:
            found.append((value, doc))
    found.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
    quoted = recall.quoter()
    rows, lines = [], []
    for value, doc in found[:SEARCH_LIMIT]:
        if doc["kind"] == "journal":
            doc = dict(doc, path=places.shown(project, places.home(project, config) / doc["id"]))
        row = {key: doc[key] for key in ("id", "kind", "type", "text", "goal", "date", "path") if doc.get(key)}
        rows.append(dict(row, score=round(value, 3)))
        lines.append(recall.item(doc, quoted))
    text = "\n".join(lines) if lines else "Nothing in memory or the journal matches."
    return success({"query": args.query, "results": rows}), ExitCode.SUCCESS, text


def _propose(project, config, args) -> Outcome:
    return propose(project, config, args.type, args.text)


def propose(project: Path, config: dict[str, Any], kind: str, text: str, *, until: str | None = None,
            replaces: str | None = None) -> Outcome:
    """A candidate for review (note --keep, memory add without a terminal)."""
    from .. import memory
    agent, task = common.current_source(project)
    row = memory.propose(project, config, kind, text, source={"agent": agent, "task_id": task}, created=common.now(),
                         until=until, replaces=replaces)
    if row is None:
        return success({"accepted": False, "candidate": None, "duplicate": True}), ExitCode.SUCCESS, \
            "Already in memory or waiting for review; nothing added."
    return success({"accepted": False, "candidate": row}), ExitCode.SUCCESS, \
        "Proposed {} for review (run agent-autosave memory review).".format(row["id"])


def _accept(project, config, args) -> Outcome:
    from .. import memory
    entry = memory.accept(project, config, args.id, today=date.today(), text=args.text)
    return success({"entry": entry}), ExitCode.SUCCESS, "Accepted into project memory as {}.".format(entry["id"])


def _reject(project, config, args) -> Outcome:
    from .. import memory
    row = memory.reject(project, config, args.id, reason=args.reason)
    return success({"candidate": row}), ExitCode.SUCCESS, "Rejected {}.".format(row["id"])


def _pin(project, config, args) -> Outcome:
    from .. import memory
    entry = memory.change_entry(project, config, args.id, "pin", today=date.today())
    return success({"entry": entry}), ExitCode.SUCCESS, "Pinned {}: shown first at session start.".format(entry["id"])


def _forget(project, config, args) -> Outcome:
    from .. import memory
    entry = memory.change_entry(project, config, args.id, "forget", today=date.today())
    return success({"forgotten": entry}), ExitCode.SUCCESS, "Removed {} from MEMORY.md.".format(entry["id"])


def _review(project, config, args) -> Outcome:
    from .. import memory
    from ..ui import style
    rows = memory.pending(project)
    listing = review_list(rows, style())
    if not rows or not interactive():
        return success({"pending": rows, "reviewed": []}), ExitCode.SUCCESS, listing
    from ..wizard import screen as screen_module, widgets
    current = style()
    screen = screen_module.Screen(current, sys.stdout)
    screen.write(listing.split("\n") + [""])
    ask = widgets.Prompter(screen)
    reviewed = []
    try:
        for row in rows:
            reviewed.append(_review_one(project, config, ask, row))
    except widgets.Cancelled:
        screen.end(current.paint("Stopped.", "yellow") + " The rest stay waiting.")
    finally:
        ask.close()
    done = [r for r in reviewed if r["action"] != "skip"]
    return success({"pending": memory.pending(project), "reviewed": reviewed}), ExitCode.SUCCESS, \
        "Reviewed {} of {} candidate{}.".format(len(done), len(rows), "" if len(rows) == 1 else "s")


def _review_one(project, config, ask, row) -> dict[str, Any]:
    from .. import memory
    from ..wizard import widgets
    choice = ask.select("{} {} ({}): {}".format(row["id"], row["type"], (row.get("source") or {}).get("agent") or "you",
                                                row["text"][:200]),
                        [widgets.Option("accept", "Accept", "add it to MEMORY.md"),
                         widgets.Option("edit", "Edit, then accept", "change the text first"),
                         widgets.Option("reject", "Reject", "never proposed again"),
                         widgets.Option("skip", "Skip", "decide later")])
    if choice == "accept":
        return {"id": row["id"], "action": "accept", "entry": memory.accept(project, config, row["id"], today=date.today())["id"]}
    if choice == "edit":
        text = ask.text("Text", default=row["text"])
        return {"id": row["id"], "action": "accept",
                "entry": memory.accept(project, config, row["id"], today=date.today(), text=text)["id"]}
    if choice == "reject":
        memory.reject(project, config, row["id"])
    return {"id": row["id"], "action": choice}


def review_list(rows: list[dict[str, Any]], style: Any) -> str:
    """Every pending candidate in one list, so contradictions are visible
    before any is accepted."""
    lines = [style.heading("agent-autosave memory review", "{} waiting".format(len(rows))), ""]
    if not rows:
        lines.append("  Nothing to review. " + style.dim("Agents' decision, tried and warning notes are proposed here "
                                                         "when a task is archived."))
        return "\n".join(lines)
    table = [[style.dim("ID"), style.dim("TYPE"), style.dim("FROM"), style.dim("TASK"), style.dim("TEXT")]]
    for row in rows:
        source = row.get("source") or {}
        table.append([row["id"], row["type"], source.get("agent") or "you", source.get("task_id") or "-",
                      row["text"][:70]])
    lines += style.table(table)
    lines += ["", style.dim("  Accept with agent-autosave memory accept <id>, reject with agent-autosave memory "
                            "reject <id>, or run this in a terminal to go through them one by one.")]
    return "\n".join(lines)


ACTIONS = {"list": _list, "add": _add, "search": _search, "propose": _propose, "review": _review, "accept": _accept, "reject": _reject, "pin": _pin,
           "forget": _forget}
