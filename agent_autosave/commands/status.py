"""`agent-autosave status`: the save on one screen."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import display
from ..contract import ExitCode, Outcome, success
from . import common

if TYPE_CHECKING:
    from ..ui import Style


def run(args: argparse.Namespace) -> Outcome:
    project = common.find_project()
    from ..config import load
    save = common.load_fresh(project)
    if save is None:
        return common.not_ready()
    if getattr(args, "all", False):
        return everything(project, save)
    config = load(project)
    stale = common.staleness(project, save, config)
    plan = save.get("plan") or []
    done = sum(item["state"] == "done" for item in plan)
    data = {"status": save["status"], "goal": save.get("goal") or "", "plan_done": done, "plan_total": len(plan),
            "next_action": save.get("next_action") or "", "task_id": save["task_id"], "updated": save["updated"],
            "interrupted": save.get("interrupted"), "failing": (save.get("failing") or {}).get("command"),
            "handoffs": save.get("handoffs") or [], "handoff_count": len(save.get("handoffs") or []),
            "stale": stale["stale"], "stale_reason": stale["reason"], "usage": usage_rows(save),
            "memory": memory_summary(project, config)}
    doing = [item["step"] for item in plan if item["state"] == "doing"]
    stopped = (display.actions(save) or [None])[0]
    from ..ui import style
    return success(data), ExitCode.SUCCESS, render(data, doing=doing[0] if doing else "", stopped=stopped or "",
                                                   style=style())


def everything(project: Path, save: dict[str, Any]) -> Outcome:
    """status --all: one row per branch slot, the current branch first."""
    from ..branches import slug
    from ..storage import read_view
    from ..ui import STATUS_COLORS, style as get_style, when
    slots = common.store(project).slots() or {"current": "", "slots": {}}
    rows = []
    for branch, task in slots["slots"].items():
        current = branch == slots["current"]
        item = save if current else read_view(Path(project) / ".autosave/slots" / (slug(branch) + ".json"))
        item = item or {}
        rows.append({"branch": branch, "current": current, "task_id": task.get("task_id"),
                     "status": item.get("status") or "-", "goal": item.get("goal") or "", "updated": item.get("updated")})
    rows.sort(key=lambda r: (not r["current"], r["branch"]))
    style = get_style()
    lines = [style.heading("agent-autosave status --all", "{} branch{}".format(len(rows), "" if len(rows) == 1 else "es"))]
    for row in rows:
        status = style.paint(row["status"], STATUS_COLORS.get(row["status"], "bold"))
        lines.append("{} {:<24} {:<10} {}  {}".format("*" if row["current"] else " ", row["branch"] or "(no git)",
                                                     status, row["task_id"], row["goal"][:60]))
        if row["updated"]:
            lines.append("    " + style.dim("updated " + when(row["updated"])))
    return success({"branches": rows}), ExitCode.SUCCESS, "\n".join(lines)


def memory_summary(project: Path, config: dict[str, Any]) -> dict[str, Any]:
    """Active entries per type and candidates waiting for review. For the
    user only: this is never injected into an agent."""
    from datetime import date
    from .. import memory
    counts = memory.counts(memory.read(project, config), date.today())
    return {"entries": {k: v for k, v in counts.items() if k != "expired"}, "expired": counts["expired"],
            "candidates": len(memory.pending(project))}


def memory_row(summary: dict[str, Any]) -> str:
    names = {"decision": "decision", "tried": "dead end", "gotcha": "gotcha", "convention": "convention"}
    parts = ["{} {}{}".format(n, names[k], "" if n == 1 else "s") for k, n in summary["entries"].items() if n]
    return ", ".join(parts) or "no entries yet"


def usage_rows(save: dict[str, Any]) -> list[str]:
    """One line per agent with a usage reading, reset time in local time."""
    from ..events import timestamp
    from ..loader import usage_text
    clock = lambda ts: timestamp(ts).astimezone().strftime("%H:%M")  # noqa: E731
    rows = [usage_text(agent, item, clock) for agent, item in sorted((save.get("usage") or {}).items())]
    return [row for row in rows if row]


def render(data: dict[str, Any], *, doing: str = "", stopped: str = "", style: Style | None = None) -> str:
    """The status screen: a heading, then aligned fields."""
    from ..ui import STATUS_COLORS, Style, when
    style = style or Style()
    status = style.paint(data["status"], "bold", STATUS_COLORS.get(data["status"], "bold"))
    lines = [style.heading("agent-autosave status", "updated " + when(data["updated"]))]
    if data["stale_reason"]:
        lines.append(style.item("warn", style.paint(data["stale_reason"], "yellow")))
    lines.append("")
    info = data.get("interrupted")
    if info:
        status += style.dim(" ({} in {} at {})".format(info.get("reason"), common.NAMES.get(info.get("agent"), info.get("agent")),
                                                       when(info.get("at"))))
    rows = [("Status", status), ("Goal", data["goal"] or style.dim("(none yet)"))]
    if data["plan_total"]:
        plan = "{} of {} done".format(data["plan_done"], data["plan_total"])
        rows.append(("Plan", plan + (style.dim(" · in progress: ") + doing if doing else "")))
    if stopped:
        rows.append(("Stopped at", stopped))
    if data["failing"]:
        rows.append(("Failing", style.paint(data["failing"], "red")))
    rows.append(("Next", style.bold(data["next_action"]) if data["next_action"] else style.dim("(none)")))
    if data.get("usage"):
        rows.append(("Usage", "\n".join(data["usage"])))
    handoffs = data["handoffs"]
    if handoffs:
        count = len(handoffs)
        text = ["{} handoff{}".format(count, "" if count == 1 else "s")]
        for item in handoffs[-5:]:
            text.append("{} {} {}  {}".format(common.NAMES.get(item["from"], item["from"]), style.sym("arrow"),
                                              common.NAMES.get(item["to"], item["to"]),
                                              style.dim("{} · {}".format(when(item["at"]), item["reason"]))))
        rows.append(("Handoffs", "\n".join(text)))
    else:
        rows.append(("Handoffs", style.dim("none")))
    summary = data.get("memory")
    if summary:
        rows.append(("Memory", memory_row(summary)))
    lines.extend(style.fields(rows))
    if summary and summary["candidates"]:
        count = summary["candidates"]
        lines.append(style.item("warn", "{} candidate{} waiting: run agent-autosave memory review".format(
            count, "" if count == 1 else "s")))
    lines.append("")
    lines.append(style.dim("  Full save: ") + style.command("agent-autosave show") + style.dim(
        "   Continue in another agent: ") + style.command("agent-autosave resume <agent>"))
    return "\n".join(lines)
