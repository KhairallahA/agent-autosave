"""`agent-autosave show`: SAVE.md, save.json, or the block an agent is given."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import TYPE_CHECKING

from ..contract import ExitCode, Outcome, success
from . import common

if TYPE_CHECKING:
    from ..ui import Style


def run(args: argparse.Namespace) -> Outcome:
    project = common.find_project()
    from ..config import load
    if args.task:
        save = common.find_save(project, args.task)
        if save is None:
            return common.unknown_task()
    else:
        save = common.load_fresh(project)
    if save is None:
        return common.not_ready()
    if getattr(args, "context", False):
        config = load(project)
        packed = common.context_explained(save, config, common.staleness(project, save, config))
        if getattr(args, "explain", False):
            return success(packed), ExitCode.SUCCESS, explain_text(packed)
        return success({"context": packed["context"]}), ExitCode.SUCCESS, packed["context"]
    if getattr(args, "json", False):
        return success(save), ExitCode.SUCCESS, json.dumps(save, indent=2, ensure_ascii=False)
    from ..render import render
    if args.task:
        markdown = render(save, load(project))  # SAVE.md is the current task's view only
    else:
        try:
            markdown = (Path(project) / ".autosave/SAVE.md").read_text(encoding="utf-8")
        except OSError:
            markdown = render(save, load(project))
    from ..ui import style
    return success({"markdown": markdown}), ExitCode.SUCCESS, highlight(markdown, style())


def explain_text(packed: dict) -> str:
    """The block, then one line per section: priority, name, size, kept or dropped."""
    rows = [packed["context"], "", "Sections ({} of {} characters used):".format(len(packed["context"]), packed["budget"])]
    for item in packed["sections"]:
        rows.append("  {:>2}  {:<14} {:>5} chars  {}".format(item["priority"], item["name"], item["chars"], item["state"]))
    return "\n".join(rows)


def highlight(markdown: str, style: Style | None = None) -> str:
    """SAVE.md for a terminal: headings and bold labels highlighted; plain text is SAVE.md exactly."""
    from ..ui import Style
    style = style or Style()
    if not style.color:
        return markdown.rstrip("\n")
    out = []
    for line in markdown.rstrip("\n").split("\n"):
        if line.startswith("<!--"):
            continue
        if line.startswith("#"):
            out.append(style.paint(line.lstrip("# "), "bold", "cyan"))
        elif line.startswith("    "):
            out.append(style.dim(line))
        else:
            out.append(re.sub(r"\*\*(.+?)\*\*", lambda m: style.bold(m.group(1)), line))
    return "\n".join(out)
