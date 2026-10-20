"""`agent-autosave statusline`: one line for Claude Code's status bar.

Claude Code runs the `statusLine` command after each assistant message and
pipes it a JSON object on stdin; whatever the command prints on stdout (exit
0) is shown. Fields used, from https://code.claude.com/docs/en/statusline :
`context_window.used_percentage`, `rate_limits.five_hour` and
`rate_limits.seven_day` (`used_percentage`, `resets_at` in Unix seconds;
absent for API users and before the first response),
`workspace.project_dir`, `cwd` and `session_id`.

It also records the usage it was shown: a `usage` event for claude-code
when a reading moved 5 points or more since the last one, so the save (and
`go`) knows how much is left. Runs on every refresh, so it stays small: no
argparse, no git, no compile. Any error prints nothing and exits 0.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

TYPE_CHECKING = False  # the status line path never imports typing (see tests/test_statusline.py)
if TYPE_CHECKING:
    from typing import Any, BinaryIO, TextIO

STEP = 5  # points a reading must move before it is recorded again
STATE = "statusline-state.json"  # the last recorded reading
WRAP = "statusline.json"  # {"command": the user's own status line, "source": where it was}
WRAP_TIMEOUT = 2.0
EMOJI = {"saved": "💾", "plan": "📋", "failing": "❌", "5h": "⏳", "7d": "📅", "ctx": "🧠"}


def _number(value: Any) -> float | None:
    return float(value) if type(value) in (int, float) and value == value and value >= 0 else None


def readings(payload: dict[str, Any]) -> dict[str, Any]:
    """The usage numbers in a status line payload; missing ones are None."""
    limits = payload.get("rate_limits") if isinstance(payload.get("rate_limits"), dict) else {}
    five = limits.get("five_hour") if isinstance(limits.get("five_hour"), dict) else {}
    week = limits.get("seven_day") if isinstance(limits.get("seven_day"), dict) else {}
    window = payload.get("context_window") if isinstance(payload.get("context_window"), dict) else {}
    resets = _number(five.get("resets_at"))
    return {"limit_pct": _number(five.get("used_percentage")), "weekly_pct": _number(week.get("used_percentage")),
            "context_pct": _number(window.get("used_percentage")),
            "limit_resets_at": datetime.fromtimestamp(resets, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if resets is not None else None}


def ago(updated: Any, now: datetime) -> str | None:
    try:
        then = datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
    except ValueError:
        return None
    seconds = max(0, int((now - then).total_seconds()))
    for size, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if seconds >= size:
            return "{}{} ago".format(seconds // size, unit)
    return "{}s ago".format(seconds)


def failing_name(command: str) -> str:
    """A failing command, short enough for a status bar: `pytest tests/x.py -q` is "pytest"."""
    words = command.split()
    if not words:
        return ""
    if len(words) > 2 and os.path.basename(words[0]).startswith("python") and words[1] == "-m":
        return words[2]
    return os.path.basename(words[0])[:24]


def segments(save: dict[str, Any] | None, usage: dict[str, Any], now: datetime) -> list[tuple[str, str]]:
    """(key, text) for each part of the line that has a value."""
    parts = []
    save = save or {}
    saved = ago(save.get("updated"), now) if save.get("updated") else None
    if saved:
        parts.append(("saved", "saved " + saved))
    plan = save.get("plan") or []
    if plan:
        parts.append(("plan", "plan {}/{}".format(sum(i.get("state") == "done" for i in plan), len(plan))))
    failing = (save.get("failing") or {}).get("command")
    if failing and failing_name(failing):
        parts.append(("failing", "failing: " + failing_name(failing)))
    for key, field in (("5h", "limit_pct"), ("7d", "weekly_pct"), ("ctx", "context_pct")):
        if usage.get(field) is not None:
            parts.append((key, "{} {}%".format(key, int(round(usage[field])))))
    return parts


def line(parts: list[tuple[str, str]], style: str) -> str:
    if style != "emoji":
        return " | ".join(text for _, text in parts)
    out = []
    for key, text in parts:
        bare = text.split(" ", 1)[1] if key in ("saved", "plan", "5h", "7d", "ctx") else text.split(": ", 1)[1]
        out.append(EMOJI[key] + " " + (key + " " + bare if key in ("5h", "7d") else bare))
    return " | ".join(out)


def moved(previous: dict[str, Any] | None, current: dict[str, Any]) -> bool:
    """True when a reading is new or moved by STEP points, or the window reset."""
    fields = ("limit_pct", "weekly_pct", "context_pct")
    if all(current[f] is None for f in fields):
        return False
    if not previous:
        return True
    if previous.get("limit_resets_at") != current["limit_resets_at"] and current["limit_resets_at"]:
        return True
    for field in fields:
        old, new = previous.get(field), current[field]
        if (old is None) != (new is None) or (new is not None and abs(new - old) >= STEP):
            return True
    return False


def record(directory: Path, payload: dict[str, Any], usage: dict[str, Any]) -> None:
    """Append a claude-code usage event when the reading moved enough."""
    from .storage import read_view, atomic_write
    state = directory / STATE
    if not moved(read_view(state), usage):
        return
    from .state import TaskStore
    session = str(payload.get("session_id") or "statusline")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    event = dict({"v": 1, "ts": stamp, "agent": "claude-code", "session": session, "turn": 0, "kind": "usage",
                  "limit_window_minutes": 300 if usage["limit_pct"] is not None else None}, **usage)
    TaskStore(directory.parent).append(event)
    atomic_write(state, dict(usage, v=1))


def wrapped(directory: Path, raw: bytes) -> str:
    """The user's own status line output, when we wrap theirs."""
    from .storage import read_view
    command = (read_view(directory / WRAP) or {}).get("command")
    if not isinstance(command, str) or not command.strip():
        return ""
    import subprocess  # only a wrapped line pays for it
    try:
        result = subprocess.run(command, shell=True, input=raw, capture_output=True, timeout=WRAP_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.decode("utf-8", "replace").rstrip("\n") if result.returncode == 0 else ""


def project_of(payload: dict[str, Any]) -> Path | None:
    workspace = payload.get("workspace") if isinstance(payload.get("workspace"), dict) else {}
    start = workspace.get("project_dir") or payload.get("cwd") or os.getcwd()
    from .capture import find_project
    project = find_project(Path(str(start)))
    return project if (project / ".autosave").is_dir() else None


def render(raw: bytes, *, now: datetime | None = None) -> str:
    """The whole status line for one payload."""
    try:
        payload = json.loads(raw.decode("utf-8")) if raw.strip() else {}
    except (UnicodeDecodeError, ValueError):
        payload = {}
    payload = payload if isinstance(payload, dict) else {}
    project = project_of(payload)
    if project is None:
        return ""
    directory = project / ".autosave"
    from .config import load
    from .storage import read_view
    usage = readings(payload)
    ours = line(segments(read_view(directory / "save.json"), usage, now or datetime.now(timezone.utc)),
                load(project)["statusline"]["style"])
    try:
        record(directory, payload, usage)
    except (OSError, ValueError):
        pass  # logged to errors.log by TaskStore; the line is still shown
    theirs = wrapped(directory, raw)
    if theirs and ours:
        lines = theirs.split("\n")
        return "\n".join(lines[:-1] + [lines[-1] + " | " + ours])
    return theirs or ours


def main(stdin: BinaryIO | None = None, stdout: TextIO | None = None) -> int:
    """Print the line; always exit 0 (Claude Code hides output on failure)."""
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout
    try:
        raw = b"" if stdin.isatty() else stdin.read(1 << 20)
        text = render(raw)
    except Exception:  # the status line boundary: fail open, like a hook
        text = ""
    if text:
        stdout.write(text + "\n")
        stdout.flush()
    return 0
