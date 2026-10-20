"""`agent-autosave history`: archived saves, newest first."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..contract import ExitCode, Outcome, success
from . import common

if TYPE_CHECKING:
    from ..ui import Style


HISTORY_MAX_BYTES = 4 * 1024 * 1024


def run(args: argparse.Namespace) -> Outcome:
    project = common.find_project()
    common.sync_memory(project)  # journal pages for tasks archived since the last sync
    pages = journal_paths(project)
    from ..storage import read_json
    folder = Path(project) / ".autosave/history"
    rows = []
    for path in (sorted(folder.glob("t_*.json")) if folder.is_dir() else []):
        try:
            if path.is_symlink() or path.stat().st_size > HISTORY_MAX_BYTES:
                continue
            item = read_json(path)
        except (OSError, ValueError):
            continue
        if not isinstance(item, dict) or item.get("v") != 1:
            continue
        plan = item.get("plan") or []
        rows.append({"task_id": item.get("task_id"), "started": item.get("started"), "updated": item.get("updated"),
                     "status": item.get("status"), "goal": (item.get("goal") or "")[:200],
                     "agents": list(dict.fromkeys(a.get("agent") for a in item.get("agents") or [])),
                     "plan_done": sum(p.get("state") == "done" for p in plan), "plan_total": len(plan),
                     "handoff_count": len(item.get("handoffs") or []), "file": path.name,
                     "journal": pages.get(item.get("task_id"))})
    rows.sort(key=lambda r: (str(r["started"]), str(r["task_id"])), reverse=True)
    if args.limit:
        rows = rows[:args.limit]
    from ..ui import style
    return success({"saves": rows}), ExitCode.SUCCESS, render(rows, style())


def journal_paths(project: Path) -> dict[str, str]:
    """task id -> its journal page path (relative to the repo, or ~ in a vault)."""
    from .. import places
    from ..config import load
    from ..memory_sync import read_state
    base = places.home(project, load(project))
    out = {}
    for task_id, known in read_state(project)["pages"].items():
        if known.get("path") and (base / known["path"]).is_file():
            out[task_id] = places.shown(project, base / known["path"])
    return out


def render(rows: list[dict[str, Any]], style: Style | None = None) -> str:
    """Archived saves as a table, newest first."""
    from ..ui import STATUS_COLORS, Style
    style = style or Style()
    if not rows:
        return "\n".join([style.heading("agent-autosave history"), "",
                          "  No archived saves yet. " + style.dim("A save is archived when you run ") +
                          style.command("agent-autosave clear") + style.dim(".")])
    table = [[style.dim("TASK"), style.dim("STATUS"), style.dim("AGENTS"), style.dim("PLAN"), style.dim("GOAL")]]
    for r in rows:
        status = str(r["status"])
        plan = "{}/{}".format(r["plan_done"], r["plan_total"]) if r["plan_total"] else "-"
        table.append([r["task_id"] or "-", style.paint(status, STATUS_COLORS.get(status, "bold")),
                      ",".join(filter(None, r["agents"])) or "-", plan, (r["goal"][:60] or style.dim("(no goal)"))])
        if r.get("journal"):
            table.append(["", "", "", "", style.dim("journal: " + r["journal"])])
    count = len(rows)
    return "\n".join([style.heading("agent-autosave history", "{} archived save{}".format(count, "" if count == 1 else "s")),
                      ""] + style.table(table))
