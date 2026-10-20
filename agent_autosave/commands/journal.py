"""`agent-autosave journal`: one Markdown page per finished or paused task.

Pages are written by the memory sync when a task is archived; this command
syncs first, so a task archived a moment ago is listed.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ..contract import ExitCode, Outcome, success
from . import common

NEWEST = 20


def run(args: argparse.Namespace) -> Outcome:
    from .. import places
    from ..memory_sync import read_state
    project = common.find_project()
    config = common.store(project).config
    common.sync_memory(project)
    base = places.home(project, config)
    if args.path:
        known = (read_state(project)["pages"].get(args.path) or {}).get("path")
        if not known or not (base / known).is_file():
            return common.unknown_task()
        shown = places.shown(project, base / known)
        return success({"task_id": args.path, "path": shown}), ExitCode.SUCCESS, shown
    pages = _pages(project, base, args.search)
    rows = pages if args.search else pages[:NEWEST]
    from ..ui import style
    return success({"pages": rows}), ExitCode.SUCCESS, render(rows, style(), search=args.search)


def _pages(project: Path, base: Path, search: str | None) -> list[dict[str, Any]]:
    """Every page, newest first; with `search`, only pages containing every word."""
    from .. import places
    from ..journal import page_day, read_frontmatter
    from ..memory_sync import local_zone
    zone = local_zone()
    words = [w.lower() for w in (search or "").split()]
    rows = []
    folder = base / "journal"
    for path in folder.rglob("*.md") if folder.is_dir() else []:
        if path.is_symlink():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if words and not all(w in text.lower() for w in words):
            continue
        head = read_frontmatter(text)
        rows.append({"task_id": head.get("task_id"), "date": page_day(head, zone),
                     "ended": head.get("ended"), "status": head.get("status"), "title": head.get("title"),
                     "path": places.shown(project, path)})
    rows.sort(key=lambda r: (str(r["ended"] or ""), r["path"]), reverse=True)
    return rows


def render(rows: list[dict[str, Any]], style: Any, *, search: str | None = None) -> str:
    title = "agent-autosave journal"
    if not rows:
        empty = "No page matches." if search else "No journal pages yet. " + style.dim(
            "A page is written when a task is finished (agent-autosave done) or archived.")
        return "\n".join([style.heading(title), "", "  " + empty])
    table = [[style.dim("DATE"), style.dim("STATUS"), style.dim("TASK"), style.dim("PAGE")]]
    for r in rows:
        table.append([r["date"] or "-", str(r["status"] or "-"), str(r["title"] or "")[:50], r["path"]])
    count = len(rows)
    return "\n".join([style.heading(title, "{} page{}".format(count, "" if count == 1 else "s")), ""]
                     + style.table(table))
