"""The journal: one Markdown page per finished or paused task.

A page is a generated view of an archived save (or of the current save once
it is done), written by the memory sync. It is rewritten when the task is
re-archived, unless its frontmatter says `locked: true`. Frontmatter scalars
are JSON-quoted so the block is valid YAML without a YAML parser, which
Obsidian and any script can read.
"""
from __future__ import annotations

from datetime import datetime, timezone, tzinfo
import json
from pathlib import Path
import re
import unicodedata
from typing import Any, Iterable, Optional

from .render import _code, _literal, _text

SLUG_MAX = 50
EMPTY = "None."
SECTIONS = ("Goal", "Outcome", "Plan", "Decisions", "Dead ends", "Checks", "Failing at the end", "Files",
            "Handoffs", "Session summary", "Memory")


def slug(goal: str) -> str:
    """The goal in lowercase ASCII, hyphenated, at most 50 characters."""
    ascii_text = unicodedata.normalize("NFKD", str(goal)).encode("ascii", "ignore").decode("ascii").lower()
    words = re.findall(r"[a-z0-9]+", ascii_text)
    out = ""
    for word in words:
        candidate = (out + "-" + word) if out else word
        if len(candidate) > SLUG_MAX:
            break
        out = candidate
    return out or (words[0][:SLUG_MAX] if words else "task")


def local_time(timestamp: str, tz: Optional[tzinfo]) -> datetime | None:
    """A recorded UTC timestamp in zone `tz` (None keeps UTC). Pure: the
    caller passes the user's zone; nothing here reads the clock."""
    try:
        moment = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(tz or timezone.utc)


def local_day(timestamp: str, tz: Optional[tzinfo]) -> str:
    """YYYY-MM-DD of `timestamp` in `tz`: the date people see."""
    moment = local_time(timestamp, tz)
    return moment.date().isoformat() if moment else str(timestamp or "")[:10]


def local_clock(timestamp: str, tz: Optional[tzinfo]) -> str:
    """`YYYY-MM-DD HH:MM` of a recorded UTC timestamp in `tz`, for notes
    people read; anything unparsable comes back unchanged."""
    moment = local_time(timestamp, tz)
    return moment.strftime("%Y-%m-%d %H:%M") if moment else str(timestamp or "")


def short_id(task_id: str, tz: Optional[tzinfo] = None) -> str:
    """The task id's time of day (HHMMSS, in `tz`), plus -N for a collision
    suffix: unique within a day, which the page's date already names."""
    match = re.fullmatch(r"t_(\d{8})_(\d{6})\d*(?:_(\d+))?", task_id)
    if not match:
        raise ValueError("Invalid task identifier.")
    clock = match.group(2)
    if tz is not None:
        moment = datetime.strptime(match.group(1) + clock, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        clock = moment.astimezone(tz).strftime("%H%M%S")
    return clock + ("-" + match.group(3) if match.group(3) else "")


def relative_path(save: dict[str, Any], taken: Iterable[str] = (), *, tz: Optional[tzinfo] = None) -> str:
    """journal/YYYY/MM/YYYY-MM-DD-<slug>-<id>.md, dated by the task's start
    in the user's zone `tz` (the sync passes it), so a re-archived task keeps
    its page. Two tasks with one goal started in the same second would share
    a name, so a path in `taken` gets -2, -3, ..."""
    day = local_day(save["started"], tz)
    stem = "journal/{}/{}/{}-{}-{}".format(day[:4], day[5:7], day, slug(save.get("goal") or ""),
                                          short_id(save["task_id"], tz))
    taken = set(taken)
    path, number = stem + ".md", 1
    while path in taken:
        number += 1
        path = "{}-{}.md".format(stem, number)
    return path


def frontmatter(save: dict[str, Any], tz: Optional[tzinfo] = None) -> dict[str, Any]:
    """`date` is the local day the task started; `started` and `ended` stay UTC."""
    git = save.get("git") or {}
    return {"title": (save.get("goal") or "Untitled task")[:200], "task_id": save["task_id"],
            "status": save.get("status"), "date": local_day(save["started"], tz), "branch": git.get("branch") or "",
            "agents": list(dict.fromkeys(a.get("agent") for a in save.get("agents") or [] if a.get("agent"))),
            "started": save["started"], "ended": save.get("updated") or save["started"],
            "commits_since_start": git.get("commits_since_start"),
            "files_changed": len(save.get("files_touched") or []), "tags": ["agent-autosave", "task"]}


def render_page(save: dict[str, Any], memory_ids: list[str], names: dict[str, str], *,
                tz: Optional[tzinfo] = None) -> str:
    """The page for `save`; `memory_ids` are the entries that came from this task."""
    head = ["---"] + ["{}: {}".format(key, json.dumps(value, ensure_ascii=False))
                      for key, value in frontmatter(save, tz).items()] + ["---"]
    title = _text(" ".join((save.get("goal") or "Untitled task").split())[:200])
    body = ["# " + title, ""]
    for name, lines in _sections(save, memory_ids, names):
        body += ["## " + name] + (lines or [EMPTY]) + [""]
    return "\n".join(head + body).rstrip() + "\n"


def _block(text: str) -> list[str]:
    """An indented code block: line breaks kept, no Markdown inside."""
    return [("    " + _literal(line)).rstrip() for line in str(text).splitlines()]


def _sections(save, memory_ids, names):
    notes = save.get("notes") or []
    plan = save.get("plan") or []
    failing = save.get("failing")
    summary = save.get("summary")
    outcome = [_text(save.get("status") or "")]
    if save.get("last_report"):
        outcome += [""] + _block(save["last_report"]) + ["", "(" + _text(names.get(save.get("last_report_agent"),
                                                                             save.get("last_report_agent") or ""))
                                                       + ", " + _text(save.get("last_report_at") or "") + ")"]
    yield "Goal", [_text(save["goal"])] if save.get("goal") else []
    yield "Outcome", outcome
    yield "Plan", ["- [" + ("x" if p["state"] == "done" else " ") + "] " + _text(p["step"]) for p in plan]
    yield "Decisions", ["- " + _text(d) for d in save.get("decisions") or []]
    yield "Dead ends", ["- " + _text(n["text"]) for n in notes if n.get("type") == "tried"]
    yield "Checks", ["- " + _code(c["command"]) + (" passed" if c["exit"] == 0 else " failing (exit {})".format(c["exit"]))
                     + " at " + _text(c["at"]) for c in save.get("checks") or []]
    yield "Failing at the end", ([_code(failing["command"]) + " (exit {})".format(failing["exit"])]
                                 if failing else [])
    yield "Files", [", ".join(_text(f) for f in save.get("files_touched") or [])] if save.get("files_touched") else []
    yield "Handoffs", ["- {} -> {} at {} ({})".format(_text(names.get(h["from"], h["from"])), _text(names.get(h["to"], h["to"])),
                                                     _text(h["at"]), _text(h["reason"]))
                       for h in save.get("handoffs") or []]
    yield "Session summary", _block(summary["text"]) if summary else []
    yield "Memory", ["- [[MEMORY#^m-{}]]".format(i) for i in memory_ids]


def page_day(head: dict[str, Any], tz: Optional[tzinfo] = None) -> str:
    """A page's local date: its `date`, or (pages written before it) the
    start converted to `tz`."""
    if isinstance(head.get("date"), str) and head["date"]:
        return head["date"]
    return local_day(str(head.get("started") or ""), tz) if head.get("started") else ""


def is_locked(path: Path) -> bool:
    """`locked: true` in the page's frontmatter: the user keeps their edits."""
    try:
        with open(str(path), encoding="utf-8") as stream:
            head = stream.read(4096)
    except OSError:
        return False
    fields = read_frontmatter(head)
    return fields.get("locked") is True


def read_frontmatter(text: str) -> dict[str, Any]:
    """The `key: value` lines between the first two `---` lines. Values are
    JSON when they parse (we write them that way); `true`/`false` and bare
    words are read as YAML would."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}
    out: dict[str, Any] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return out
        key, sep, value = line.partition(":")
        if not sep or not key.strip():
            continue
        value = value.strip()
        try:
            out[key.strip()] = json.loads(value)
        except ValueError:
            out[key.strip()] = value
    return {}


def section_text(page: str, names: tuple = ("Goal", "Decisions", "Dead ends", "Outcome")) -> str:
    """The text under the named headings, for the recall index."""
    out, keep = [], False
    for line in page.split("\n"):
        if line.startswith("## "):
            keep = line[3:].strip() in names
            continue
        if keep and line.strip() and line.strip() != EMPTY:
            out.append(line.strip())
    return "\n".join(out)
