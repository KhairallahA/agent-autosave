"""`agent-autosave watch`: a live screen of every branch slot, the current
save, usage per agent, the last action, the failing command and memory
candidates waiting for review. Refreshes every 2 seconds; q quits.

curses when stdout is a terminal that supports it, else the screen is
cleared and redrawn with plain text. `--once` prints one frame and exits,
for scripts and tests."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any, Callable

from .. import display
from ..contract import ExitCode, Outcome, success
from . import common

REFRESH = 2.0


def snapshot(project: Path) -> dict[str, Any]:
    """Everything one frame shows, read fresh from disk."""
    from ..storage import read_view
    from .status import usage_rows
    from .. import memory
    from ..branches import slug
    directory = Path(project) / ".autosave"
    save = common.load_fresh(project) or {}  # recompiled: tool calls and readings since the last turn end
    slots = common.store(project).slots() or {"current": "", "slots": {}}
    rows = []
    for branch, task in sorted(slots["slots"].items(), key=lambda kv: (kv[0] != slots["current"], kv[0])):
        item = save if branch == slots["current"] else read_view(directory / "slots" / (slug(branch) + ".json")) or {}
        rows.append({"branch": branch, "current": branch == slots["current"], "task_id": task.get("task_id"),
                     "status": item.get("status") or "-"})
    plan = save.get("plan") or []
    return {"status": save.get("status") or "-", "goal": save.get("goal") or "", "updated": save.get("updated"),
            "plan_done": sum(i.get("state") == "done" for i in plan), "plan_total": len(plan),
            "slots": rows, "usage": usage_rows(save) if save else [],
            "last_action": (display.actions(save) or [""])[0] if save else "",
            "failing": (save.get("failing") or {}).get("command"),
            "candidates": len(memory.pending(project))}


def frame(data: dict[str, Any], width: int = 100) -> list[str]:
    """The screen as plain lines, cut to the terminal width."""
    from ..ui import when
    lines = ["agent-autosave watch" + ("   updated " + when(data["updated"]) if data["updated"] else "")
             + "   (q quits)", ""]
    lines.append("Status    {}{}".format(data["status"], "   plan {}/{}".format(data["plan_done"], data["plan_total"])
                                         if data["plan_total"] else ""))
    lines.append("Goal      " + (data["goal"] or "(none yet)"))
    lines.append("Last      " + (data["last_action"] or "(nothing yet)"))
    lines.append("Failing   " + (data["failing"] or "nothing"))
    lines.append("Memory    {} candidate{} waiting".format(data["candidates"], "" if data["candidates"] == 1 else "s"))
    lines.append("")
    lines.append("Usage")
    lines.extend("  " + row for row in data["usage"] or ["(no readings yet)"])
    lines.append("")
    lines.append("Slots")
    for row in data["slots"] or [{"current": True, "branch": "", "status": data["status"], "task_id": ""}]:
        lines.append("{} {:<28} {:<12} {}".format("*" if row["current"] else " ", row["branch"] or "(no git)",
                                                  row["status"], row["task_id"] or ""))
    return [line[:max(width - 1, 20)] for line in lines]


def run(args: argparse.Namespace) -> Outcome:
    project = common.find_project()
    data = snapshot(project)
    if getattr(args, "once", False) or getattr(args, "json", False) or not sys.stdout.isatty():
        return success(data), ExitCode.SUCCESS, "\n".join(frame(data))
    try:
        import curses
    except ImportError:
        curses = None
    if curses is not None:
        try:
            curses.wrapper(lambda screen: _curses_loop(screen, project))
            return success({}), ExitCode.SUCCESS, None
        except curses.error:
            pass  # no usable terminal description: draw plain text
    _plain_loop(project, _key_reader())
    return success({}), ExitCode.SUCCESS, None


def _curses_loop(screen: Any, project: Path) -> None:
    import curses
    curses.curs_set(0)
    screen.timeout(int(REFRESH * 1000))
    while True:
        height, width = screen.getmaxyx()
        screen.erase()
        for row, text in enumerate(frame(snapshot(project), width)[:height]):
            screen.addstr(row, 0, text)
        screen.refresh()
        if screen.getch() in (ord("q"), ord("Q")):
            return


def _key_reader() -> Callable[[float], str]:
    """A function that waits up to N seconds for a key ("" on timeout).
    Plain mode needs Enter after q where the terminal cannot go raw."""
    import select

    def read(timeout: float) -> str:
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        return sys.stdin.readline().strip() if ready else ""
    return read


def _plain_loop(project: Path, read_key: Callable[[float], str]) -> None:
    import shutil
    while True:
        width = shutil.get_terminal_size((100, 30)).columns
        sys.stdout.write("\033[2J\033[H" + "\n".join(frame(snapshot(project), width)) + "\n")
        sys.stdout.flush()
        if read_key(REFRESH).lower().startswith("q"):
            return
